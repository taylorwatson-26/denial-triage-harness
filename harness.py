"""
Deterministic layer of the denial triage harness.

Everything here is plain Python: the lookup tools the LLM calls, the
guardrails that gate its output, the priority math, and the eval. No model
calls happen in this file. That split is deliberate -- ranking arithmetic
done inside a prompt is not reproducible run to run, and a worklist that
reorders itself for no reason is a worklist nobody trusts.
"""

import csv
from datetime import date, datetime
from pathlib import Path

TODAY = date(2026, 8, 7)
DATA = Path(__file__).parent / "data"

DISPOSITIONS = {"rebill_corrected", "appeal", "bill_patient", "write_off"}

# Guardrail constants -- tune these on camera to show the harness responding.
AUTO_DISPOSITION_CEILING = 500.0   # dollars
AMBIGUITY_CONFIDENCE_FLOOR = 0.75
DEADLINE_CLIFF_DAYS = 15
AGING_FLOOR_DAYS = 90


def _read(name):
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f))


CARC_TABLE = {r["carc"]: r for r in _read("carc_reference.csv")}
PAYER_TABLE = {r["payer"]: r for r in _read("payer_filing.csv")}
OVERTURN_TABLE = {(r["carc"], r["payer"]): float(r["overturn_rate"])
                  for r in _read("overturn_rates.csv")}
TOUCH_TABLE = {r["disposition"]: int(r["avg_touch_minutes"])
               for r in _read("touch_minutes.csv")}


# ------------------------------------------------------------------ tools
# These four functions are the tool surface handed to the model. Each one
# returns "not found" rather than guessing, which is what makes the
# unknown-code guardrail possible at all.

def lookup_carc(carc: str) -> dict:
    row = CARC_TABLE.get(carc)
    if not row:
        return {"found": False, "carc": carc,
                "note": "Code is not in the reference table. Do not infer a meaning."}
    return {"found": True, "carc": carc, "description": row["description"],
            "standard_disposition": row["standard_disposition"],
            "ambiguity": row["ambiguity"]}


def lookup_payer_filing(payer: str) -> dict:
    row = PAYER_TABLE.get(payer)
    if not row:
        return {"found": False, "payer": payer}
    return {"found": True, "payer": payer,
            "timely_filing_days": int(row["timely_filing_days"])}


def lookup_overturn_rate(carc: str, payer: str) -> dict:
    rate = OVERTURN_TABLE.get((carc, payer))
    if rate is None:
        return {"found": False, "carc": carc, "payer": payer}
    return {"found": True, "carc": carc, "payer": payer, "overturn_rate": rate}


def lookup_touch_minutes(disposition: str) -> dict:
    mins = TOUCH_TABLE.get(disposition)
    if mins is None:
        return {"found": False, "disposition": disposition}
    return {"found": True, "disposition": disposition, "avg_touch_minutes": mins}


TOOLS = {
    "lookup_carc": lookup_carc,
    "lookup_payer_filing": lookup_payer_filing,
    "lookup_overturn_rate": lookup_overturn_rate,
    "lookup_touch_minutes": lookup_touch_minutes,
}


# ------------------------------------------------------------- claim prep

def load_denials():
    claims = []
    for r in _read("denials.csv"):
        dos = datetime.strptime(r["dos"], "%Y-%m-%d").date()
        window = PAYER_TABLE.get(r["payer"])
        window_days = int(window["timely_filing_days"]) if window else 0
        claims.append({
            "claim_id": r["claim_id"],
            "patient_acct": r["patient_acct"],
            "payer": r["payer"],
            "cpt": r["cpt"],
            "charge_amount": float(r["charge_amount"]),
            "dos": dos,
            "carc": r["carc"],
            "rarc": r["rarc"],
            "days_in_ar": int(r["days_in_ar"]),
            "days_to_deadline": (dos.toordinal() + window_days) - TODAY.toordinal(),
        })
    return claims


# ------------------------------------------------------------- guardrails

def apply_guardrails(claim: dict, proposal: dict,
                     ceiling: float = AUTO_DISPOSITION_CEILING) -> dict:
    """
    Gate an LLM disposition proposal.

    Returns status one of:
      auto          -- safe to place on a specialist worklist
      needs_review  -- a human confirms before action
      blocked       -- the system refuses to disposition at all
    """
    reasons = []
    disposition = proposal.get("disposition")
    confidence = float(proposal.get("confidence", 0.0))
    status = "auto"

    def escalate(new_status, reason):
        nonlocal status, reasons
        rank = {"auto": 0, "needs_review": 1, "blocked": 2}
        if rank[new_status] > rank[status]:
            status = new_status
        reasons.append(reason)

    # G1 -- unknown CARC. Refuse outright rather than let the model
    # reason from the code's shape.
    if claim["carc"] not in CARC_TABLE:
        escalate("blocked", f"G1: CARC {claim['carc']} is not in the reference table")

    # G2 -- proposal must be one of the four known dispositions.
    if disposition not in DISPOSITIONS:
        escalate("blocked", f"G2: proposed disposition '{disposition}' is not recognized")

    # G3 -- timely filing already expired. Never auto-close these; a
    # supervisor owns the write-off and the root-cause question.
    if claim["days_to_deadline"] <= 0:
        escalate("blocked", "G3: timely filing window has expired; supervisor sign-off required")

    # G4 -- no unsupervised write-offs at any dollar amount.
    if disposition == "write_off":
        escalate("needs_review", "G4: write-off recommendations require human sign-off")

    # G5 -- dollar ceiling on auto-disposition.
    if claim["charge_amount"] > ceiling:
        escalate("needs_review",
                 f"G5: charge ${claim['charge_amount']:,.2f} exceeds the "
                 f"${ceiling:,.0f} auto-disposition ceiling")

    # G6 -- ambiguous codes need a confident model or a human.
    ref = CARC_TABLE.get(claim["carc"])
    if ref and ref["ambiguity"] == "ambiguous" and confidence < AMBIGUITY_CONFIDENCE_FLOOR:
        escalate("needs_review",
                 f"G6: {claim['carc']} maps to multiple actions and model "
                 f"confidence was {confidence:.2f}")

    return {"status": status, "disposition": disposition,
            "confidence": confidence, "guardrail_reasons": reasons,
            "model_rationale": proposal.get("rationale", "")}


# ---------------------------------------------------------------- scoring

def urgency_multiplier(days_to_deadline: int) -> float:
    if days_to_deadline <= DEADLINE_CLIFF_DAYS:
        return 2.0
    if days_to_deadline <= 30:
        return 1.5
    if days_to_deadline <= 60:
        return 1.2
    return 1.0


def score_claim(claim: dict, gated: dict, aging_floor: bool = True) -> dict:
    """
    Expected recoverable value per minute of specialist effort, adjusted
    for deadline pressure. Deterministic -- same inputs, same order, every
    run.
    """
    disposition = gated["disposition"]
    overturn = OVERTURN_TABLE.get((claim["carc"], claim["payer"]), 0.0)

    # Blocked and review items still need an effort estimate for capacity
    # planning; they just cost a review touch instead of a work touch.
    effort_key = disposition if gated["status"] == "auto" else "human_review"
    touch = TOUCH_TABLE.get(effort_key, TOUCH_TABLE["human_review"])

    expected_value = claim["charge_amount"] * overturn
    base_score = expected_value / touch
    mult = urgency_multiplier(claim["days_to_deadline"])
    final_score = base_score * mult

    # Tier 0 beats every score. Money past a filing deadline is gone
    # permanently, so no ranking function is allowed to outvote it.
    if 0 < claim["days_to_deadline"] <= DEADLINE_CLIFF_DAYS:
        tier = 0
        tier_reason = f"{claim['days_to_deadline']} days to timely filing"
    elif aging_floor and claim["days_in_ar"] > AGING_FLOOR_DAYS:
        tier = 1
        tier_reason = f"aged {claim['days_in_ar']} days in A/R"
    else:
        tier = 2
        tier_reason = ""

    return {**claim, **gated,
            "overturn_rate": overturn,
            "expected_value": round(expected_value, 2),
            "touch_minutes": touch,
            "urgency_multiplier": mult,
            "final_score": round(final_score, 2),
            "tier": tier,
            "tier_reason": tier_reason}


def rank(scored: list) -> list:
    return sorted(scored, key=lambda c: (c["tier"], -c["final_score"]))


# ------------------------------------------------------------- assignment

def build_worklists(ranked: list):
    specialists = []
    for r in _read("specialists.csv"):
        specialists.append({
            "name": r["specialist"],
            "skills": set(r["skills"].split("|")),
            "remaining": int(r["daily_capacity_minutes"]),
            "items": [],
        })

    review_queue, blocked_queue, unassigned = [], [], []

    for claim in ranked:
        if claim["status"] == "blocked":
            blocked_queue.append(claim)
            continue
        if claim["status"] == "needs_review":
            review_queue.append(claim)
            continue
        placed = False
        for s in specialists:
            if claim["disposition"] in s["skills"] and s["remaining"] >= claim["touch_minutes"]:
                s["items"].append(claim)
                s["remaining"] -= claim["touch_minutes"]
                placed = True
                break
        if not placed:
            unassigned.append(claim)

    return specialists, review_queue, blocked_queue, unassigned


# -------------------------------------------------------------------- eval

def evaluate(scored: list, top_n: int = 20) -> dict:
    """
    Does the harness ranking beat a naive sort by charge amount?

    Metric that matters: expected dollars captured in the top N, and how
    much of the at-risk expected value (deadline inside 30 days) makes the
    cut. A ranking that captures value but misses the cliff is worse than
    useless, because the cliff dollars are unrecoverable.
    """
    harness_top = rank(scored)[:top_n]
    dollar_top = sorted(scored, key=lambda c: -c["charge_amount"])[:top_n]

    at_risk_total = sum(c["expected_value"] for c in scored
                        if 0 < c["days_to_deadline"] <= 30)

    def summarize(items, label):
        at_risk = sum(c["expected_value"] for c in items
                      if 0 < c["days_to_deadline"] <= 30)
        return {
            "ranking": label,
            "expected_value_captured": round(sum(c["expected_value"] for c in items), 2),
            "at_risk_value_captured": round(at_risk, 2),
            "at_risk_pct": round(100 * at_risk / at_risk_total, 1) if at_risk_total else 0.0,
            "minutes_required": sum(c["touch_minutes"] for c in items),
            "cliff_claims_included": sum(1 for c in items
                                         if 0 < c["days_to_deadline"] <= DEADLINE_CLIFF_DAYS),
        }

    cliff_total = sum(1 for c in scored if 0 < c["days_to_deadline"] <= DEADLINE_CLIFF_DAYS)
    return {
        "top_n": top_n,
        "at_risk_value_total": round(at_risk_total, 2),
        "cliff_claims_total": cliff_total,
        "harness": summarize(harness_top, "harness"),
        "baseline": summarize(dollar_top, "charge_amount_desc"),
    }


def starvation_report(scored: list, top_n: int = 20) -> list:
    """Claims that never surface in the daily top N. The gap the harness
    only partly closes -- see README."""
    top_ids = {c["claim_id"] for c in rank(scored)[:top_n]}
    return [c for c in scored if c["claim_id"] not in top_ids]

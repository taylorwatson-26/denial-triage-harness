"""
Agent layer + CLI.

Two modes:

  --offline (default)  A scripted stand-in for the model. Deterministic,
                       needs no API key, and reproduces the same failure
                       modes every run -- which is what you want when you
                       are recording a demo take five times.

  --live               Real Claude tool-use loop against the four lookup
                       tools in harness.py. Needs ANTHROPIC_API_KEY.

Usage:
  python agent.py                       full run, offline
  python agent.py --live                full run, real model
  python agent.py --break unknown       show the unknown-CARC refusal
  python agent.py --break bundling      show the error guardrails MISS
  python agent.py --break starvation    show the claims that never surface
  python agent.py --no-aging-floor      starvation with the floor removed
"""

import argparse
import json
import os
import sys

import harness as H

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a denial triage assistant for a healthcare revenue cycle team.

For the claim you are given, choose exactly one disposition:
  rebill_corrected  fix the claim and resubmit to the same payer
  appeal            formal appeal with supporting documentation
  bill_patient      transfer balance to patient responsibility
  write_off         close the balance as unrecoverable

Rules:
- Always call lookup_carc before deciding. Never infer what a denial code
  means from its format or from memory.
- If lookup_carc returns found=false, you must return disposition "unknown"
  and confidence 0.0. Do not guess.
- Call lookup_payer_filing and lookup_overturn_rate before finalizing.
- Report honest confidence. Codes marked ambiguity="ambiguous" map to more
  than one defensible action; say so and score your confidence accordingly.

Respond with ONLY a JSON object, no prose and no markdown fences:
{"disposition": "...", "confidence": 0.0, "rationale": "one sentence"}"""

TOOL_SCHEMAS = [
    {"name": "lookup_carc",
     "description": "Look up a CARC denial code in the organization's reference table.",
     "input_schema": {"type": "object",
                      "properties": {"carc": {"type": "string"}},
                      "required": ["carc"]}},
    {"name": "lookup_payer_filing",
     "description": "Get the timely filing window in days for a payer.",
     "input_schema": {"type": "object",
                      "properties": {"payer": {"type": "string"}},
                      "required": ["payer"]}},
    {"name": "lookup_overturn_rate",
     "description": "Historical probability that pursuing this CARC with this payer recovers payment.",
     "input_schema": {"type": "object",
                      "properties": {"carc": {"type": "string"},
                                     "payer": {"type": "string"}},
                      "required": ["carc", "payer"]}},
    {"name": "lookup_touch_minutes",
     "description": "Average specialist minutes required for a disposition type.",
     "input_schema": {"type": "object",
                      "properties": {"disposition": {"type": "string"}},
                      "required": ["disposition"]}},
]


# ------------------------------------------------------------- offline mock

# One deliberate defect, mirroring a real model failure: CO-97 (service
# bundled into another payment) usually needs a corrected rebill with an
# appropriate modifier, not a formal appeal. The mock proposes "appeal"
# with high confidence. Both seeded CO-97 claims sit under the $500
# ceiling, so no guardrail fires. This is the documented gap.
MOCK_OVERRIDES = {"CO-97": ("appeal", 0.88,
                            "Payer bundled this service; appealing the bundling edit.")}


def mock_disposition(claim):
    ref = H.lookup_carc(claim["carc"])
    if not ref["found"]:
        # An ungoverned model confabulates here. That is the whole point of
        # the demo -- it returns something plausible and confident.
        return {"disposition": "appeal", "confidence": 0.81,
                "rationale": "Payer denied the line; pursuing an appeal.",
                "tool_calls": ["lookup_carc"]}

    if claim["carc"] in MOCK_OVERRIDES:
        disp, conf, why = MOCK_OVERRIDES[claim["carc"]]
        return {"disposition": disp, "confidence": conf, "rationale": why,
                "tool_calls": ["lookup_carc", "lookup_overturn_rate"]}

    conf = 0.62 if ref["ambiguity"] == "ambiguous" else 0.91
    return {"disposition": ref["standard_disposition"], "confidence": conf,
            "rationale": ref["description"][:70],
            "tool_calls": ["lookup_carc", "lookup_payer_filing", "lookup_overturn_rate"]}


# ---------------------------------------------------------------- live loop

def live_disposition(claim, client, verbose=False):
    user = (f"Claim {claim['claim_id']}: payer {claim['payer']}, CPT {claim['cpt']}, "
            f"charge ${claim['charge_amount']:,.2f}, date of service {claim['dos']}, "
            f"denial code {claim['carc']} (RARC {claim['rarc']}), "
            f"{claim['days_in_ar']} days in A/R.")
    messages = [{"role": "user", "content": user}]
    calls = []

    for _ in range(6):
        resp = client.messages.create(model=MODEL, max_tokens=1024,
                                      system=SYSTEM_PROMPT, tools=TOOL_SCHEMAS,
                                      messages=messages)
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())
            except json.JSONDecodeError:
                parsed = {"disposition": "unparseable", "confidence": 0.0,
                          "rationale": text[:120]}
            parsed["tool_calls"] = calls
            return parsed

        results = []
        for tu in tool_uses:
            calls.append(tu.name)
            if verbose:
                print(f"      tool -> {tu.name}({tu.input})")
            out = H.TOOLS[tu.name](**tu.input)
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})

    return {"disposition": "unknown", "confidence": 0.0,
            "rationale": "Agent loop hit its step limit.", "tool_calls": calls}


# -------------------------------------------------------------------- run

def run(live=False, aging_floor=True, verbose=False,
        ceiling=H.AUTO_DISPOSITION_CEILING):
    claims = H.load_denials()
    client = None
    if live:
        try:
            import anthropic
        except ImportError:
            sys.exit("pip install anthropic, or drop --live to use the mock.")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Set ANTHROPIC_API_KEY, or drop --live to use the mock.")
        client = anthropic.Anthropic()

    scored = []
    for c in claims:
        if verbose:
            print(f"  {c['claim_id']} {c['carc']:<7} ${c['charge_amount']:>8,.2f}")
        proposal = live_disposition(c, client, verbose) if live else mock_disposition(c)
        gated = H.apply_guardrails(c, proposal, ceiling=ceiling)
        gated["tool_calls"] = proposal.get("tool_calls", [])
        scored.append(H.score_claim(c, gated, aging_floor=aging_floor))
    return scored


# ----------------------------------------------------------------- reports

def money(x):
    return f"${x:,.2f}"


def print_worklists(scored):
    specialists, review, blocked, unassigned = H.build_worklists(H.rank(scored))

    print("\n=== SPECIALIST WORKLISTS " + "=" * 45)
    for s in specialists:
        used = sum(i["touch_minutes"] for i in s["items"])
        print(f"\n{s['name']}  --  {len(s['items'])} items, {used} min used, "
              f"{s['remaining']} min left")
        for i in s["items"][:8]:
            flag = f"  [{i['tier_reason']}]" if i["tier_reason"] else ""
            print(f"   {i['claim_id']}  {i['carc']:<7} {money(i['charge_amount']):>10} "
                  f"x {i['overturn_rate']:.2f} = EV {money(i['expected_value']):>9}  "
                  f"score {i['final_score']:>8.1f}{flag}")
        if len(s["items"]) > 8:
            print(f"   ... {len(s['items']) - 8} more")

    print(f"\n=== SUPERVISOR REVIEW QUEUE ({len(review)}) " + "=" * 34)
    for i in review:
        print(f"   {i['claim_id']}  {i['carc']:<7} {money(i['charge_amount']):>10}  "
              f"proposed: {i['disposition']}")
        for r in i["guardrail_reasons"]:
            print(f"      guardrail: {r}")

    print(f"\n=== BLOCKED ({len(blocked)}) " + "=" * 48)
    for i in blocked:
        print(f"   {i['claim_id']}  {i['carc']:<7} {money(i['charge_amount']):>10}")
        for r in i["guardrail_reasons"]:
            print(f"      guardrail: {r}")

    if unassigned:
        print(f"\n=== NO CAPACITY TODAY ({len(unassigned)}) " + "=" * 38)
        for i in unassigned[:10]:
            print(f"   {i['claim_id']}  {i['disposition']:<18} score {i['final_score']:.1f}")


def print_eval(scored, top_n=20):
    e = H.evaluate(scored, top_n)
    print(f"\n=== EVAL: harness ranking vs sort-by-dollar (top {top_n}) " + "=" * 16)
    print(f"At-risk expected value in the full batch: {money(e['at_risk_value_total'])} "
          f"across {e['cliff_claims_total']} cliff claims\n")
    hdr = f"{'ranking':<22}{'EV captured':>14}{'at-risk':>13}{'at-risk %':>11}{'cliff':>7}{'min':>7}"
    print(hdr)
    print("-" * len(hdr))
    for k in ("harness", "baseline"):
        r = e[k]
        print(f"{r['ranking']:<22}{money(r['expected_value_captured']):>14}"
              f"{money(r['at_risk_value_captured']):>13}{r['at_risk_pct']:>10.1f}%"
              f"{r['cliff_claims_included']:>7}{r['minutes_required']:>7}")
    delta = (e["harness"]["at_risk_value_captured"]
             - e["baseline"]["at_risk_value_captured"])
    print(f"\nAt-risk dollars the harness captures that a dollar sort misses: {money(delta)}")


def break_unknown(scored):
    print("\n=== FAILURE MODE 1: unknown denial code (CAUGHT) " + "=" * 21)
    print("The mock model confabulates a confident disposition for a code")
    print("that does not exist in the reference table. G1 refuses it.\n")
    for c in scored:
        if any(r.startswith("G1") for r in c["guardrail_reasons"]):
            print(f"   {c['claim_id']}  CARC {c['carc']}  {money(c['charge_amount'])}")
            print(f"      model proposed : {c['disposition']} at {c['confidence']:.2f} confidence")
            print(f"      model said     : {c['model_rationale']}")
            print(f"      harness status : {c['status'].upper()}")
            for r in c["guardrail_reasons"]:
                print(f"      guardrail      : {r}")
            print()


def break_bundling(scored):
    print("\n=== FAILURE MODE 2: wrong-but-plausible mapping (NOT CAUGHT) " + "=" * 9)
    print("CO-97 means the service was bundled into another payment. The")
    print("usual fix is a corrected rebill with the right modifier, not a")
    print("formal appeal. The model proposes appeal, confidently. Both")
    print("claims sit under the $500 ceiling, the code is in the reference")
    print("table, and the disposition is a legal value -- so every guardrail")
    print("passes it through to a worklist.\n")
    for c in scored:
        if c["carc"] == "CO-97":
            print(f"   {c['claim_id']}  {money(c['charge_amount']):>9}  "
                  f"proposed {c['disposition']} at {c['confidence']:.2f}  "
                  f"status {c['status'].upper()}")
            print(f"      cost of the error: {H.TOUCH_TABLE['appeal']} min of appeals time "
                  f"instead of {H.TOUCH_TABLE['rebill_corrected']} min of rebill time")
    print("\nWhy no guardrail catches it: every rule in apply_guardrails checks")
    print("the SHAPE of the proposal (known code, legal value, dollar amount,")
    print("confidence). None of them check whether the mapping is CORRECT.")
    print("Catching this needs an eval against actual A/R outcomes -- which is")
    print("the first thing to build in the Week 7 capstone.")


def break_starvation(scored, aging_floor, top_n=20):
    never = H.starvation_report(scored, top_n)
    small = sorted(never, key=lambda c: c["charge_amount"])[:8]
    label = "WITH aging floor" if aging_floor else "WITHOUT aging floor"
    print(f"\n=== FAILURE MODE 3: starvation ({label}) " + "=" * 20)
    print(f"{len(never)} of {len(scored)} claims never reach the top {top_n}.")
    print("Smallest ones, and how long they have been sitting:\n")
    for c in small:
        print(f"   {c['claim_id']}  {money(c['charge_amount']):>8}  "
              f"{c['days_in_ar']:>4} days in A/R  "
              f"{c['days_to_deadline']:>4} days to deadline  "
              f"score {c['final_score']:>6.1f}")
    aged = [c for c in never if c["days_in_ar"] > H.AGING_FLOOR_DAYS]
    print(f"\nOf those, {len(aged)} are already past {H.AGING_FLOOR_DAYS} days in A/R.")
    if aging_floor:
        print("The aging floor promotes them to tier 1 -- but only after day 90.")
        print("Between day 30 and day 90 nothing surfaces them, and small-dollar")
        print("claims in that band quietly age toward a write-off.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="use the real Claude API")
    p.add_argument("--no-aging-floor", action="store_true")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--break", dest="brk",
                   choices=["unknown", "bundling", "starvation", "all"])
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()

    aging_floor = not a.no_aging_floor
    mode = "LIVE (Claude API)" if a.live else "OFFLINE (scripted model)"
    print(f"Denial triage harness -- {mode}, aging floor "
          f"{'ON' if aging_floor else 'OFF'}")

    scored = run(live=a.live, aging_floor=aging_floor, verbose=a.verbose)

    counts = {}
    for c in scored:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    print(f"\n{len(scored)} claims processed -- "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))

    if a.brk in (None, "all"):
        print_worklists(scored)
        print_eval(scored, a.top)
    if a.brk in ("unknown", "all"):
        break_unknown(scored)
    if a.brk in ("bundling", "all"):
        break_bundling(scored)
    if a.brk in ("starvation", "all"):
        break_starvation(scored, aging_floor, a.top)


if __name__ == "__main__":
    main()

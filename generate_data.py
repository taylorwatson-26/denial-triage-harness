"""
Generate synthetic RCM denial fixtures for the harness demo.

SYNTHETIC DATA ONLY. No PHI. Every patient account, claim ID, and dollar
amount below is fabricated. Regenerate any time with: python generate_data.py

Edge cases are hand-seeded (not random) so the guardrail and starvation
demos fire reliably on camera. Filler rows are seeded-random for volume.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

TODAY = date(2026, 8, 7)
DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
random.seed(1206)

# ---------------------------------------------------------------- payers

PAYERS = {
    "Highmark BCBS": 365,
    "Medicare Part B": 365,
    "Aetna": 180,
    "Geisinger Health Plan": 120,
    "UnitedHealthcare": 90,
}

PAYER_MODIFIER = {
    "Highmark BCBS": 1.05,
    "Medicare Part B": 0.95,
    "Aetna": 1.00,
    "Geisinger Health Plan": 1.10,
    "UnitedHealthcare": 0.85,
}

# ------------------------------------------------------- CARC reference
# NOTE: CO-B7 and CO-234 appear in denials.csv but are deliberately
# ABSENT here. That gap is the unknown-code guardrail demo.

CARC = {
    "CO-16": ("Claim lacks information or has submission/billing error",
              "rebill_corrected", 0.72, "ambiguous"),
    "CO-97": ("Benefit for this service is included in another service already adjudicated",
              "rebill_corrected", 0.35, "clear"),
    "CO-45": ("Charge exceeds fee schedule / contracted maximum",
              "write_off", 0.08, "clear"),
    "CO-50": ("Not deemed a medical necessity by the payer",
              "appeal", 0.42, "clear"),
    "CO-109": ("Claim not covered by this payer or contractor",
               "bill_patient", 0.15, "clear"),
    "CO-11": ("Diagnosis is inconsistent with the procedure",
              "rebill_corrected", 0.78, "clear"),
    "CO-18": ("Exact duplicate claim or service",
              "write_off", 0.55, "clear"),
    "CO-22": ("Care may be covered by another payer per coordination of benefits",
              "rebill_corrected", 0.80, "clear"),
    "CO-29": ("Time limit for filing has expired",
              "write_off", 0.05, "clear"),
    "CO-197": ("Precertification, authorization, or notification absent",
               "appeal", 0.38, "clear"),
    "CO-4": ("Procedure code inconsistent with the modifier used or required modifier missing",
             "rebill_corrected", 0.85, "clear"),
    "CO-151": ("Payer does not support this many or this frequency of services",
               "appeal", 0.30, "clear"),
    "PR-1": ("Deductible amount", "bill_patient", 0.90, "clear"),
    "PR-2": ("Coinsurance amount", "bill_patient", 0.90, "clear"),
    "PR-3": ("Copayment amount", "bill_patient", 0.92, "clear"),
}

RARC = {
    "CO-16": "M76", "CO-97": "N19", "CO-45": "N10", "CO-50": "N115",
    "CO-109": "N418", "CO-11": "M64", "CO-18": "N522", "CO-22": "MA04",
    "CO-29": "N211", "CO-197": "N54", "CO-4": "M78", "CO-151": "M86",
    "PR-1": "N130", "PR-2": "N130", "PR-3": "N130",
    "CO-B7": "N570", "CO-234": "N390",
}

TOUCH_MINUTES = {
    "rebill_corrected": 8,
    "appeal": 45,
    "bill_patient": 3,
    "write_off": 2,
    "human_review": 12,
}

CPT_CHARGE = {
    "99213": 145, "99214": 210, "99215": 295, "93000": 88,
    "71046": 132, "80053": 64, "20610": 178, "29881": 4850,
    "45378": 1240, "70450": 920, "97110": 96, "11042": 385,
    "66984": 3200, "64483": 1680,
}


def dos_for(payer, days_to_deadline):
    """Work backwards from a target deadline distance to a date of service."""
    return TODAY + timedelta(days=days_to_deadline) - timedelta(days=PAYERS[payer])


def row(n, payer, cpt, carc, days_to_deadline, charge=None):
    dos = dos_for(payer, days_to_deadline)
    return {
        "claim_id": f"CLM-{26000 + n}",
        "patient_acct": f"ACCT-{random.randint(400000, 499999)}",
        "payer": payer,
        "cpt": cpt,
        "charge_amount": charge if charge is not None else CPT_CHARGE[cpt],
        "dos": dos.isoformat(),
        "carc": carc,
        "rarc": RARC.get(carc, ""),
        "days_in_ar": max(5, (TODAY - dos).days - 14),
    }


rows = []
n = 0

def add(*args, **kw):
    global n
    n += 1
    rows.append(row(n, *args, **kw))


# --- SEEDED EDGE CASES -------------------------------------------------

# Unknown CARC codes -> guardrail must refuse to disposition (2 rows)
add("Aetna", "45378", "CO-B7", 88)
add("Highmark BCBS", "11042", "CO-234", 140)

# Timely-filing cliff, all small-dollar so a dollar-sort buries them (4 rows)
add("UnitedHealthcare", "97110", "CO-16", 4)
add("Geisinger Health Plan", "99213", "CO-4", 9)
add("UnitedHealthcare", "80053", "CO-11", 12)
add("Aetna", "11042", "CO-16", 14, charge=612)

# Already expired -> write-off candidate, must be blocked to supervisor (1 row)
add("UnitedHealthcare", "99214", "CO-29", -6)

# Starvation candidates: small dollar, aged, comfortable deadline (6 rows)
add("Highmark BCBS", "80053", "PR-2", 210)
add("Highmark BCBS", "97110", "PR-1", 195)
add("Medicare Part B", "93000", "PR-2", 188)
add("Highmark BCBS", "99213", "CO-109", 176)
add("Medicare Part B", "80053", "PR-3", 205)
add("Highmark BCBS", "97110", "PR-1", 168)

# CO-97 bundling, under the $500 ceiling -> the plausible-but-wrong
# disposition the guardrails will NOT catch (2 rows)
add("Aetna", "20610", "CO-97", 96)
add("Geisinger Health Plan", "99215", "CO-97", 74)

# High-dollar appeals -> trip the $500 auto-disposition ceiling (4 rows)
add("Medicare Part B", "29881", "CO-50", 250)
add("Aetna", "66984", "CO-197", 118)
add("Geisinger Health Plan", "64483", "CO-50", 62)
add("Highmark BCBS", "45378", "CO-151", 290)

# Extra CO-16 ambiguity, mid-dollar (2 rows)
add("Geisinger Health Plan", "70450", "CO-16", 55)
add("Medicare Part B", "99215", "CO-16", 300)

# --- FILLER ------------------------------------------------------------

filler_carcs = ["CO-16", "CO-97", "CO-45", "CO-50", "CO-109", "CO-11",
                "CO-18", "CO-22", "CO-197", "CO-4", "CO-151",
                "PR-1", "PR-2", "PR-3"]

while n < 40:
    payer = random.choice(list(PAYERS))
    cpt = random.choice(list(CPT_CHARGE))
    carc = random.choice(filler_carcs)
    dtd = random.randint(25, int(PAYERS[payer] * 0.85))
    add(payer, cpt, carc, dtd)

with open(DATA / "denials.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# ------------------------------------------------------- reference files

with open(DATA / "carc_reference.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["carc", "description", "standard_disposition", "base_overturn_rate", "ambiguity"])
    for code, (desc, disp, rate, amb) in CARC.items():
        w.writerow([code, desc, disp, rate, amb])

with open(DATA / "payer_filing.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["payer", "timely_filing_days", "overturn_modifier"])
    for payer, days in PAYERS.items():
        w.writerow([payer, days, PAYER_MODIFIER[payer]])

with open(DATA / "overturn_rates.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["carc", "payer", "overturn_rate"])
    for code, (_, _, base, _) in CARC.items():
        for payer, mod in PAYER_MODIFIER.items():
            w.writerow([code, payer, round(min(0.95, max(0.02, base * mod)), 3)])

with open(DATA / "touch_minutes.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["disposition", "avg_touch_minutes"])
    for disp, mins in TOUCH_MINUTES.items():
        w.writerow([disp, mins])

with open(DATA / "specialists.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["specialist", "skills", "daily_capacity_minutes"])
    w.writerow(["M. Reyes", "appeal", 420])
    w.writerow(["D. Okafor", "rebill_corrected|write_off", 420])
    w.writerow(["S. Lang", "bill_patient|rebill_corrected", 300])

print(f"Wrote {len(rows)} denials + 5 reference tables to {DATA}")

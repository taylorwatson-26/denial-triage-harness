# Denial Triage & Worklist Builder

A small harness project for Discussion 6C. Takes a batch of denials, dispositions each one under guardrails, and produces ranked, capacity-balanced worklists for a three-person A/R follow-up team.

**All data is synthetic.** No PHI. Every claim ID, account number, payer mix, and dollar amount is fabricated by `generate_data.py`. The overturn rates are plausible but invented — do not cite them as benchmarks.

---

## Task and user

**Task:** route inbound denials to one of four dispositions (`rebill_corrected`, `appeal`, `bill_patient`, `write_off`), then sequence the resulting work so specialists touch the highest-recovery items first without missing timely-filing deadlines.

**User:** an A/R follow-up team of three — an appeals specialist, a rebill/edits specialist, and a patient-billing specialist — plus the supervisor who owns the review queue.

## Harness elements

| Element | Where |
|---|---|
| **Tool use** | Four lookup functions in `harness.py` (`lookup_carc`, `lookup_payer_filing`, `lookup_overturn_rate`, `lookup_touch_minutes`), exposed to Claude as tool schemas in `agent.py`. The model is instructed never to recall a denial code from memory. |
| **Guardrails** | `apply_guardrails()` — six rules, G1 through G6. Unknown code, illegal disposition, expired filing, no unsupervised write-offs, $500 auto-disposition ceiling, ambiguous-code confidence floor. |
| **Agent loop** | `live_disposition()` — a real plan-act-observe loop, capped at six turns, that calls tools and feeds results back before committing to a disposition. |
| **Eval** | `evaluate()` — scores the harness ranking against a naive sort-by-dollar baseline on expected value captured and at-risk dollars protected. |

Four elements against a requirement of two. If you're short on time, tool use plus guardrails is the pair to defend.

## Run it

```bash
python generate_data.py          # writes six CSVs into data/
python agent.py                  # full run, scripted model, no API key needed
python agent.py --live           # real Claude tool-use loop (needs ANTHROPIC_API_KEY)
python agent.py --live --verbose # prints every tool call as it fires
```

Demos:

```bash
python agent.py --break unknown              # guardrail catches confabulation
python agent.py --break bundling             # guardrail MISSES a wrong mapping
python agent.py --break starvation           # small claims that never surface
python agent.py --break starvation --no-aging-floor   # same, floor removed
python agent.py --break all                  # everything
```

`--offline` is the default and is fully deterministic — same output every run. Record your takes against it, then do one `--live` pass to prove the loop is real.

## Dashboard

```bash
python build_dashboard.py     # writes dashboard.html + snapshot.json
```

Then double-click `dashboard.html`. No server, no network, no API key — everything is precomputed and embedded.

The Python harness stays the single source of truth. `build_dashboard.py` runs the batch across four guardrail ceilings × aging floor on/off and embeds all eight results; the page switches between them rather than recalculating anything in JavaScript. Nothing in the browser can disagree with what the CLI prints.

Three things to drive on camera:

- **Guardrail rail (left).** Live hit counts per rule. Click one to highlight every claim it caught and dim the rest.
- **Ceiling control.** $250 → $2,500 moves G5 from 19 hits to 6 and the cleared queue from 18 claims to 23. That's the staffing trade-off made visible in one click.
- **Aging floor toggle.** Flip it off and the count of aged-past-90 claims stranded below the top 20 goes from 2 to 10.

## Architecture, and why it's split this way

The LLM does classification. Deterministic Python does arithmetic. Ranking math inside a prompt is not reproducible run to run, and a worklist that reorders itself for no reason is one nobody trusts. Say this in Act 1 — it's the design decision most likely to draw a question.

Scoring:

```
expected_value = charge_amount × overturn_rate(carc, payer)
base_score     = expected_value / touch_minutes(disposition)
final_score    = base_score × urgency_multiplier(days_to_deadline)
```

Expected recovery per minute of specialist effort. Then two hard overrides that beat any score:

- **Tier 0 — deadline cliff.** Inside 15 days of timely filing, jump the queue. Money past a filing deadline is unrecoverable, so no ranking function gets to outvote it.
- **Tier 1 — aging floor.** Past 90 days in A/R, force into consideration regardless of value.

## The four demos

**1. Unknown CARC — caught.** `CO-B7` and `CO-234` appear in the denial file and are deliberately absent from the reference table. The model proposes `appeal` at 0.81 confidence with a fluent rationale. G1 blocks both. Point out that the model isn't malfunctioning — it's doing exactly what a language model does with an unfamiliar token, and that's precisely why the check has to live outside the model.

**2. CO-97 bundling — not caught.** CO-97 means the service was bundled into another payment; the usual fix is a corrected rebill with the right modifier, not a formal appeal. The model proposes `appeal` at 0.88 confidence. Both seeded claims sit under $500, the code is in the table, and `appeal` is a legal value — so all six guardrails pass it through onto Reyes's worklist. Cost: 45 minutes of appeals time instead of 8 minutes of rebill time, twice.

This is the honest gap. Every rule in `apply_guardrails` checks the *shape* of the proposal. None check whether the mapping is *correct*. Catching it requires an eval against actual A/R outcomes, which is exactly what 7A should build.

**3. Starvation — partly caught.** 20 of 40 claims never reach the top 20. Toggle `--no-aging-floor` and the number of aged-past-90 claims stuck below the fold goes from 2 to 10. The floor works — but only at day 90. Between day 30 and day 90 nothing surfaces small-dollar claims, and they quietly age toward write-off.

**4. Review-queue volume — an unglamorous finding worth showing.** At a $500 ceiling, 18 of 40 claims land in the supervisor queue. The guardrail is doing its job, and its job costs a supervisor half the batch. Raise the constant on camera and re-run to show the trade-off. A guardrail nobody can afford to staff gets switched off in week two.

## The eval result — read it carefully before you record

```
ranking                  EV captured      at-risk  at-risk %  cliff    min
harness                   $12,233.28    $3,733.17     100.0%      4    236
charge_amount_desc        $17,643.82    $3,320.64      88.9%      1    273
```

The baseline captures **more** total expected value. The harness wins on the thing that's irreversible: all four cliff claims instead of one, and $412 of at-risk value the dollar sort leaves to expire — in fewer specialist minutes.

Don't hide this. "My ranking loses on the headline number and wins on the number that can't be recovered later" is a stronger three minutes of video than a rigged win, and it sets up an obvious next iteration: a blended objective that weights recoverable-later dollars below expire-this-week dollars.

## Video mapping

- **Act 1 (~90s)** — architecture diagram, the four harness elements, the LLM/deterministic split, and what you left out: no MCP connection to a live worklist, because that means real PHI in a vendor tool. Scope decision, not an omission. Cite your Week 6 reading here.
- **Act 2 (~3min)** — walk `harness.py`, then break it twice: `--break unknown` (caught), `--break bundling` (missed). Then the `--no-aging-floor` toggle.
- **Act 3 (~2.5min)** — `python agent.py --live --verbose` so tool calls print in real time, then switch to `dashboard.html`: walk the guardrail rail, drag the ceiling from $250 to $2,500, flip the aging floor, and land on the ranking check.
- **Close** — name the first 7A iteration: an outcome-based eval that would have caught the CO-97 error.

## Capstone extensions (Path A)

1. **Outcome eval** — score dispositions against actual A/R resolution, which closes the gap demo #2 exposes.
2. **MCP server** over the denial worklist so specialists query conversationally, read-only, service account, de-identified.
3. **Appeal letter generation** for tier-0 appeals, with the rubric eval from your earlier draft as the gate.
4. **Blended ranking objective** that discounts recoverable-later dollars against expire-this-week dollars.

## Files

```
generate_data.py     fixture generator; edge cases hand-seeded, filler seeded-random
harness.py           tools, guardrails, scoring, assignment, eval — no model calls
agent.py             LLM loop, scripted mock, CLI and reports
build_dashboard.py   runs all eight scenarios, emits the self-contained dashboard
dashboard.html       open this one; single file, works offline
snapshot.json        same data, if you want it for something else
data/                denials.csv + five reference tables
```

## Known limits

- Overturn rates are invented. In production these come from your own resolved-claim history, and getting them wrong silently misranks everything from that payer — a population-level failure no per-item guardrail catches.
- Assignment is greedy, not optimal. It fills the first matching specialist with capacity; it does not balance across the team or handle partial-day carryover.
- One denial per claim. Real remittances stack multiple CARCs per line.
- `TODAY` is pinned to 2026-08-07 in both files so the deadline math stays reproducible. Change it in both places or the fixtures drift.

"""
Build a self-contained dashboard from precomputed harness runs.

The Python harness stays the single source of truth: every number in the
dashboard is computed here, not recalculated in JavaScript. We run the
batch across four guardrail ceilings x aging floor on/off, embed all eight
results, and let the page switch between them. Open dashboard.html by
double-clicking -- no server, no network, no API key.

    python build_dashboard.py
"""

import json
from pathlib import Path

import agent as A
import harness as H

CEILINGS = [250, 500, 1000, 2500]
TOP_N = 20

CLAIM_FIELDS = ["claim_id", "payer", "cpt", "carc", "charge_amount",
                "days_in_ar", "days_to_deadline", "disposition", "confidence",
                "status", "overturn_rate", "expected_value", "touch_minutes",
                "urgency_multiplier", "final_score", "tier", "tier_reason",
                "model_rationale"]


def trim(c):
    d = {k: c[k] for k in CLAIM_FIELDS}
    d["reasons"] = c["guardrail_reasons"]
    return d


def scenario(ceiling, aging_floor):
    scored = A.run(live=False, aging_floor=aging_floor, ceiling=ceiling)
    ranked = H.rank(scored)
    specialists, review, blocked, unassigned = H.build_worklists(ranked)

    counts = {f"G{i}": 0 for i in range(1, 7)}
    for c in scored:
        for r in c["guardrail_reasons"]:
            gid = r.split(":")[0]
            if gid in counts:
                counts[gid] += 1

    return {
        "ceiling": ceiling,
        "aging_floor": aging_floor,
        "claims": [trim(c) for c in ranked],
        "worklists": [{"name": s["name"],
                       "skills": sorted(s["skills"]),
                       "used": sum(i["touch_minutes"] for i in s["items"]),
                       "remaining": s["remaining"],
                       "items": [i["claim_id"] for i in s["items"]]}
                      for s in specialists],
        "review": [c["claim_id"] for c in review],
        "blocked": [c["claim_id"] for c in blocked],
        "unassigned": [c["claim_id"] for c in unassigned],
        "guardrail_counts": counts,
        "eval": H.evaluate(scored, TOP_N),
        "starved": [c["claim_id"] for c in H.starvation_report(scored, TOP_N)],
    }


GUARDRAILS = [
    ("G1", "Unknown denial code", "Refuse rather than let the model reason from the code's shape."),
    ("G2", "Illegal disposition", "The proposal must be one of the four defined actions."),
    ("G3", "Filing window expired", "A supervisor owns the write-off and the root cause."),
    ("G4", "Write-off proposed", "No balance closes without a human signing for it."),
    ("G5", "Over the dollar ceiling", "Above this amount, a person confirms before work is queued."),
    ("G6", "Ambiguous code, low confidence", "Codes with more than one defensible action need a confident model."),
]

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Denial triage and worklist builder</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --ground:#EEF1F4; --surface:#fff; --ink:#16212C; --muted:#61748A;
  --rule:#CCD6E0; --steel:#24506E;
  --auto:#0F6E56; --auto-bg:#E3F1EB;
  --review:#96600A; --review-bg:#FAEFDB;
  --blocked:#A02D2D; --blocked-bg:#F9E7E7;
  --display:"Instrument Sans",system-ui,sans-serif;
  --body:"Source Sans 3",system-ui,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--body);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1220px;margin:0 auto;padding:0 20px 64px}

header{border-bottom:2px solid var(--steel);padding:34px 0 20px;margin-bottom:26px}
.eyebrow{font-family:var(--display);font-size:11px;font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;color:var(--steel);margin:0 0 8px}
h1{font-family:var(--display);font-weight:600;font-size:34px;letter-spacing:-.02em;margin:0 0 6px}
.sub{color:var(--muted);margin:0;max-width:64ch}
.batchline{display:flex;flex-wrap:wrap;gap:22px;margin-top:18px;font-family:var(--mono);font-size:12px}
.batchline b{font-weight:500;font-size:19px;display:block;letter-spacing:-.02em}
.batchline span{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:10px}

.cols{display:grid;grid-template-columns:266px 1fr;gap:30px;align-items:start}

/* signature element: the guardrail ladder */
.rail{background:var(--surface);border:1px solid var(--rule);position:sticky;top:20px}
.rail h2{font-family:var(--display);font-size:11px;font-weight:600;letter-spacing:.14em;
  text-transform:uppercase;margin:0;padding:14px 16px;border-bottom:1px solid var(--rule);
  color:var(--steel)}
.gr{padding:12px 16px;border-bottom:1px solid #E6ECF1;cursor:pointer;background:none;
  border-left:0;border-right:0;border-top:0;width:100%;text-align:left;font:inherit;display:block}
.gr:last-child{border-bottom:0}
.gr:hover,.gr:focus-visible{background:#F5F8FA;outline:none}
.gr[aria-pressed="true"]{background:#EAF0F5;box-shadow:inset 3px 0 0 var(--steel)}
.gr-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.gr-id{font-family:var(--mono);font-size:11px;color:var(--steel);font-weight:500}
.gr-n{font-family:var(--mono);font-size:15px;font-weight:500}
.gr-n.zero{color:#A9B6C4}
.gr-name{font-size:13px;margin:1px 0 7px;line-height:1.3}
.meter{height:5px;background:#E6ECF1;overflow:hidden}
.meter i{display:block;height:100%;background:var(--steel);width:0;
  transition:width .45s cubic-bezier(.2,.7,.3,1)}
.gr-why{font-size:12px;color:var(--muted);line-height:1.4;margin:8px 0 0;display:none}
.gr[aria-pressed="true"] .gr-why{display:block}

.controls{background:var(--surface);border:1px solid var(--rule);padding:16px 18px;
  display:flex;flex-wrap:wrap;gap:26px;align-items:center;margin-bottom:20px}
.ctl{display:flex;flex-direction:column;gap:5px}
.ctl label{font-family:var(--display);font-size:10px;font-weight:600;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted)}
.seg{display:flex;border:1px solid var(--rule)}
.seg button{font-family:var(--mono);font-size:12px;padding:5px 11px;background:var(--surface);
  border:0;border-right:1px solid var(--rule);cursor:pointer;color:var(--muted)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--steel);color:#fff}
.seg button:focus-visible{outline:2px solid var(--steel);outline-offset:-2px}
.note{font-size:12.5px;color:var(--muted);flex:1;min-width:220px;border-left:2px solid var(--rule);
  padding-left:14px}

.tabs{display:flex;gap:0;border-bottom:1px solid var(--rule);margin-bottom:0}
.tabs button{font-family:var(--display);font-size:13px;font-weight:500;padding:9px 16px;
  background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;color:var(--muted)}
.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--steel)}
.tabs button:focus-visible{outline:2px solid var(--steel);outline-offset:-2px}
.panel{background:var(--surface);border:1px solid var(--rule);border-top:0;padding:20px}

.wl{margin-bottom:26px}
.wl:last-child{margin-bottom:0}
.wl-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  border-bottom:1px solid var(--rule);padding-bottom:7px;margin-bottom:4px}
.wl-name{font-family:var(--display);font-size:16px;font-weight:600}
.wl-cap{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.capbar{height:4px;background:#E6ECF1;margin-bottom:10px}
.capbar i{display:block;height:100%;background:var(--auto);transition:width .45s ease}

table{width:100%;border-collapse:collapse;font-size:13.5px}
th{font-family:var(--display);font-size:10px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);text-align:left;padding:5px 9px 5px 0;
  border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:7px 9px 7px 0;border-bottom:1px solid #EEF2F5;vertical-align:top}
tr:last-child td{border-bottom:0}
.num{font-family:var(--mono);text-align:right;white-space:nowrap;font-size:12.5px}
.id{font-family:var(--mono);font-size:12.5px}
.dim{color:var(--muted)}
.hit{background:#FFF8E6}

.tag{display:inline-block;font-family:var(--mono);font-size:10.5px;padding:1px 6px;
  border:1px solid;letter-spacing:.02em;white-space:nowrap}
.t-auto{color:var(--auto);background:var(--auto-bg);border-color:#B9DACC}
.t-review{color:var(--review);background:var(--review-bg);border-color:#E7CE98}
.t-blocked{color:var(--blocked);background:var(--blocked-bg);border-color:#E2B5B5}
.t-tier{color:var(--steel);background:#EAF0F5;border-color:#BFD0DE}

.reason{font-family:var(--mono);font-size:11.5px;color:var(--muted);display:block;
  padding-left:11px;border-left:2px solid var(--rule);margin-top:4px}
.reason.g-hit{border-left-color:var(--review);color:var(--review)}

.evalgrid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px}
.card{border:1px solid var(--rule);padding:15px 17px}
.card.win{border-color:var(--steel);border-width:2px;padding:14px 16px}
.card h3{font-family:var(--display);font-size:12px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;margin:0 0 12px;color:var(--muted)}
.card.win h3{color:var(--steel)}
.metric{display:flex;justify-content:space-between;gap:12px;padding:5px 0;
  border-bottom:1px solid #EEF2F5;font-size:13.5px}
.metric:last-child{border-bottom:0}
.metric b{font-family:var(--mono);font-weight:500}
.verdict{border-left:3px solid var(--steel);padding:2px 0 2px 16px;margin:0;
  font-size:14.5px;max-width:76ch}
.verdict strong{font-weight:600}
h3.sec{font-family:var(--display);font-size:12px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);margin:26px 0 10px}
.empty{color:var(--muted);font-style:italic;padding:14px 0}

@media (max-width:900px){
  .cols{grid-template-columns:1fr}
  .rail{position:static}
  .evalgrid{grid-template-columns:1fr}
  h1{font-size:27px}
}
@media (prefers-reduced-motion:reduce){*{transition:none !important}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <p class="eyebrow">Revenue cycle &middot; A/R follow-up</p>
  <h1>Denial triage and worklist builder</h1>
  <p class="sub">A batch of denials, dispositioned by a language model under six guardrails,
  then ranked by recoverable value per minute of specialist effort. Synthetic data only.</p>
  <div class="batchline">
    <div><b id="s-total">0</b><span>claims in batch</span></div>
    <div><b id="s-auto">0</b><span>cleared to a worklist</span></div>
    <div><b id="s-review">0</b><span>held for review</span></div>
    <div><b id="s-blocked">0</b><span>refused outright</span></div>
    <div><b id="s-value">$0</b><span>expected value at stake</span></div>
  </div>
</header>

<div class="cols">
  <aside class="rail">
    <h2>Guardrails</h2>
    <div id="rail"></div>
  </aside>

  <main>
    <div class="controls">
      <div class="ctl">
        <label id="lab-ceiling">Auto-disposition ceiling</label>
        <div class="seg" id="seg-ceiling" role="group" aria-labelledby="lab-ceiling"></div>
      </div>
      <div class="ctl">
        <label id="lab-floor">Aging floor at 90 days</label>
        <div class="seg" id="seg-floor" role="group" aria-labelledby="lab-floor">
          <button data-floor="1" aria-pressed="true">On</button>
          <button data-floor="0" aria-pressed="false">Off</button>
        </div>
      </div>
      <p class="note" id="note"></p>
    </div>

    <div class="tabs" role="tablist">
      <button role="tab" data-tab="work" aria-selected="true">Worklists</button>
      <button role="tab" data-tab="review" aria-selected="false">Review queue</button>
      <button role="tab" data-tab="blocked" aria-selected="false">Refused</button>
      <button role="tab" data-tab="eval" aria-selected="false">Ranking check</button>
    </div>
    <div class="panel" id="panel"></div>
  </main>
</div>
</div>

<script>
const DATA = __DATA__;
const GUARDRAILS = __GUARDRAILS__;
const CEILINGS = __CEILINGS__;

let ceiling = 500, floorOn = 1, tab = "work", focusG = null;

const key = () => ceiling + "_" + (floorOn ? "on" : "off");
const S = () => DATA[key()];
const byId = () => Object.fromEntries(S().claims.map(c => [c.claim_id, c]));
const usd = n => "$" + n.toLocaleString("en-US", {minimumFractionDigits:0, maximumFractionDigits:0});
const usd2 = n => "$" + n.toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2});
const esc = s => String(s).replace(/[&<>]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[m]));

function statusTag(s){
  const map = {auto:["t-auto","cleared"], needs_review:["t-review","review"], blocked:["t-blocked","refused"]};
  const [cls, label] = map[s];
  return `<span class="tag ${cls}">${label}</span>`;
}

function reasons(c){
  return c.reasons.map(r => {
    const gid = r.split(":")[0];
    const hit = focusG && gid === focusG ? " g-hit" : "";
    return `<span class="reason${hit}">${esc(r)}</span>`;
  }).join("");
}

function buildRail(){
  const counts = S().guardrail_counts;
  const max = Math.max(1, ...Object.values(counts));
  document.getElementById("rail").innerHTML = GUARDRAILS.map(([id, name, why]) => {
    const n = counts[id];
    const pressed = focusG === id;
    return `<button class="gr" data-g="${id}" aria-pressed="${pressed}">
      <span class="gr-top"><span class="gr-id">${id}</span>
      <span class="gr-n${n === 0 ? " zero" : ""}">${n}</span></span>
      <span class="gr-name">${esc(name)}</span>
      <span class="meter"><i style="width:${Math.round(100*n/max)}%"></i></span>
      <span class="gr-why">${esc(why)}</span></button>`;
  }).join("");
  document.querySelectorAll(".gr").forEach(b => b.onclick = () => {
    focusG = focusG === b.dataset.g ? null : b.dataset.g;
    render();
  });
}

function claimRow(c, opts = {}){
  const flagged = focusG && c.reasons.some(r => r.startsWith(focusG + ":"));
  const tier = c.tier_reason ? `<span class="tag t-tier">${esc(c.tier_reason)}</span>` : "";
  return `<tr class="${flagged ? "hit" : ""}">
    <td class="id">${c.claim_id}</td>
    <td>${esc(c.payer)}<br><span class="dim id">${c.carc}</span></td>
    <td>${esc(c.disposition)} ${tier}
      ${opts.reasons ? reasons(c) : ""}</td>
    <td class="num">${usd(c.charge_amount)}</td>
    <td class="num">${c.overturn_rate.toFixed(2)}</td>
    <td class="num">${usd(c.expected_value)}</td>
    <td class="num">${c.touch_minutes}</td>
    <td class="num">${c.final_score.toFixed(1)}</td>
  </tr>`;
}

const HEAD = `<tr><th>Claim</th><th>Payer / code</th><th>Action</th><th>Charge</th>
  <th>Odds</th><th>Value</th><th>Min</th><th>Score</th></tr>`;

function renderWork(){
  const m = byId();
  return S().worklists.map(w => {
    const cap = w.used + w.remaining;
    const rows = w.items.map(id => claimRow(m[id])).join("");
    return `<section class="wl">
      <div class="wl-head">
        <span class="wl-name">${esc(w.name)}</span>
        <span class="wl-cap">${w.items.length} items &middot; ${w.used} of ${cap} min</span>
      </div>
      <div class="capbar"><i style="width:${Math.round(100*w.used/cap)}%"></i></div>
      ${rows ? `<table>${HEAD}${rows}</table>` : `<p class="empty">Nothing queued.</p>`}
    </section>`;
  }).join("");
}

function renderQueue(ids, emptyMsg){
  const m = byId();
  if(!ids.length) return `<p class="empty">${emptyMsg}</p>`;
  return `<table>${HEAD}${ids.map(id => claimRow(m[id], {reasons:true})).join("")}</table>`;
}

function renderEval(){
  const e = S().eval, h = e.harness, b = e.baseline;
  const delta = h.at_risk_value_captured - b.at_risk_value_captured;
  const card = (r, title, win) => `<div class="card${win ? " win" : ""}">
    <h3>${title}</h3>
    <div class="metric"><span>Expected value in top ${e.top_n}</span><b>${usd2(r.expected_value_captured)}</b></div>
    <div class="metric"><span>Of that, expiring within 30 days</span><b>${usd2(r.at_risk_value_captured)}</b></div>
    <div class="metric"><span>Share of all expiring value</span><b>${r.at_risk_pct.toFixed(1)}%</b></div>
    <div class="metric"><span>Claims inside the filing cliff</span><b>${r.cliff_claims_included} of ${e.cliff_claims_total}</b></div>
    <div class="metric"><span>Specialist minutes required</span><b>${r.minutes_required}</b></div>
  </div>`;
  return `<div class="evalgrid">
      ${card(h, "This ranking", true)}
      ${card(b, "Sorted by charge amount", false)}
    </div>
    <p class="verdict">Sorting by dollar amount captures
    <strong>${usd2(b.expected_value_captured - h.expected_value_captured)} more</strong>
    expected value in the same top ${e.top_n} &mdash; and leaves
    <strong>${usd2(delta)}</strong> to expire past timely filing, unrecoverable at any
    later date. It reaches ${b.cliff_claims_included} of ${e.cliff_claims_total} claims
    inside the filing cliff; this ranking reaches ${h.cliff_claims_included}, in
    ${b.minutes_required - h.minutes_required} fewer specialist minutes.</p>
    <h3 class="sec">Never surfaced &mdash; ${S().starved.length} claims below the top ${e.top_n}</h3>
    ${renderStarved()}`;
}

function renderStarved(){
  const m = byId();
  const rows = S().starved.map(id => m[id])
    .sort((a, b) => a.charge_amount - b.charge_amount).slice(0, 10);
  const aged = S().starved.map(id => m[id]).filter(c => c.days_in_ar > 90).length;
  return `<table><tr><th>Claim</th><th>Payer / code</th><th>Charge</th>
    <th>Days in A/R</th><th>Days to deadline</th><th>Score</th></tr>
    ${rows.map(c => `<tr class="${c.days_in_ar > 90 ? "hit" : ""}">
      <td class="id">${c.claim_id}</td>
      <td>${esc(c.payer)}<br><span class="dim id">${c.carc}</span></td>
      <td class="num">${usd(c.charge_amount)}</td>
      <td class="num">${c.days_in_ar}</td>
      <td class="num">${c.days_to_deadline}</td>
      <td class="num">${c.final_score.toFixed(1)}</td></tr>`).join("")}</table>
    <p class="verdict" style="margin-top:14px">${aged} of them are already past 90 days in A/R.
    ${floorOn
      ? "The aging floor lifts those into view &mdash; but only at day 90. Between day 30 and day 90 nothing surfaces a small-dollar claim, and it ages toward a write-off in silence."
      : "With the floor off, nothing lifts them at all. Turn it on to see how many the rule recovers."}</p>`;
}

function render(){
  const s = S(), m = byId();
  const counts = {auto:0, needs_review:0, blocked:0};
  s.claims.forEach(c => counts[c.status]++);
  document.getElementById("s-total").textContent = s.claims.length;
  document.getElementById("s-auto").textContent = counts.auto;
  document.getElementById("s-review").textContent = counts.needs_review;
  document.getElementById("s-blocked").textContent = counts.blocked;
  document.getElementById("s-value").textContent =
    usd(s.claims.reduce((t, c) => t + c.expected_value, 0));

  buildRail();

  const pct = Math.round(100 * counts.needs_review / s.claims.length);
  document.getElementById("note").innerHTML = focusG
    ? `Highlighting every claim held by <b>${focusG}</b>. Click it again to clear.`
    : `At a ${usd(ceiling)} ceiling, ${counts.needs_review} of ${s.claims.length} claims
       (${pct}%) wait on a supervisor. A guardrail nobody can staff gets switched off.`;

  document.querySelectorAll("#seg-ceiling button").forEach(b =>
    b.setAttribute("aria-pressed", +b.dataset.c === ceiling));
  document.querySelectorAll("#seg-floor button").forEach(b =>
    b.setAttribute("aria-pressed", +b.dataset.floor === floorOn));
  document.querySelectorAll(".tabs button").forEach(b =>
    b.setAttribute("aria-selected", b.dataset.tab === tab));

  const p = document.getElementById("panel");
  if(tab === "work") p.innerHTML = renderWork();
  else if(tab === "review") p.innerHTML = renderQueue(s.review,
    "No claim is waiting on a supervisor at this ceiling.");
  else if(tab === "blocked") p.innerHTML = renderQueue(s.blocked,
    "The harness dispositioned every claim in the batch.");
  else p.innerHTML = renderEval();
}

document.getElementById("seg-ceiling").innerHTML = CEILINGS.map(c =>
  `<button data-c="${c}" aria-pressed="${c === ceiling}">${usd(c)}</button>`).join("");
document.querySelectorAll("#seg-ceiling button").forEach(b =>
  b.onclick = () => { ceiling = +b.dataset.c; render(); });
document.querySelectorAll("#seg-floor button").forEach(b =>
  b.onclick = () => { floorOn = +b.dataset.floor; render(); });
document.querySelectorAll(".tabs button").forEach(b =>
  b.onclick = () => { tab = b.dataset.tab; render(); });

render();
</script>
</body>
</html>
"""


def main():
    snapshots = {}
    for ceiling in CEILINGS:
        for floor in (True, False):
            snapshots[f"{ceiling}_{'on' if floor else 'off'}"] = scenario(ceiling, floor)

    html = (TEMPLATE
            .replace("__DATA__", json.dumps(snapshots, separators=(",", ":")))
            .replace("__GUARDRAILS__", json.dumps(GUARDRAILS))
            .replace("__CEILINGS__", json.dumps(CEILINGS)))

    out = Path(__file__).parent / "dashboard.html"
    out.write_text(html)

    snap = Path(__file__).parent / "snapshot.json"
    snap.write_text(json.dumps(snapshots, indent=1))

    print(f"Wrote {out.name} ({len(html)//1024} KB) and {snap.name} "
          f"across {len(snapshots)} scenarios")


if __name__ == "__main__":
    main()

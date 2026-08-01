"""
obsidian_logger.py  --  Writes one markdown note per morning into your vault,
in a format that drops straight into your existing journaling workflow.
Takes the full snapshot so it can log every read.
"""
import datetime as dt
import os

import config


def log_to_obsidian(snap: dict) -> str:
    bias, gex, levels = snap["bias"], snap["gex"], snap["levels"]
    em = snap["expected_move"]
    folder = os.path.join(config.OBSIDIAN_VAULT, config.OBSIDIAN_SUBFOLDER)
    os.makedirs(folder, exist_ok=True)
    today = dt.datetime.now(config.TZ).strftime("%Y-%m-%d")
    path = os.path.join(folder, f"{today}.md")

    conf = snap.get("expiry_confluence", [])
    conf_lines = "\n".join(
        f"- **{c['type']} {c['price']}** — {c['count']}/{c['total']} expiries" + (" ✓ full" if c['full'] else "")
        for c in conf) or "- (none)"
    map_lines = "\n".join(f"| {r['price']} | {r['role']} | {r['tag']} |" for r in snap.get("level_map", []))

    admon = "warning" if bias["conviction"] == "reduced" else "note"
    ratio = snap.get("ratio")
    nq = (lambda p: f"{round(p * ratio):,}" if (ratio and p is not None) else "—")
    tr = snap.get("track", {})
    tr_line = (f"**Track record:** {tr.get('dir_hit_rate')}% directional over {tr.get('dir_n', 0)} "
               f"({tr.get('hit_rate')}% overall, {tr.get('n', 0)} graded)\n") if tr.get("n") else ""
    md = f"""---
date: {today}
type: nyam-bias
ticker: {config.PRIMARY_TICKER}
bias: {bias['label']}
score: {bias['score']}
conviction: {bias['conviction']}
tags: [trading, nyam, bias]
---

# NYAM Bias — {today}

> [!{admon}] {bias['label']} ({bias['score']})
> {bias['summary']}

**Expected move:** ±${em['dollars']} ({em['pct']}%) → range {em['low']}–{em['high']}
**Regime:** {gex['regime']} gamma · **Net GEX:** {round(gex['net_gex'])}
{tr_line}

## Level Action Map
| Price | Role | Action |
|---|---|---|
{map_lines}

## Key GEX levels
| Level | QQQ | NQ ≈ |
|---|---|---|
| Spot | {gex['spot']} | {nq(gex['spot'])} |
| Control node / magnet | {gex['control_node']} | {nq(gex['control_node'])} |
| Gamma flip | {gex['gamma_flip']} | {nq(gex['gamma_flip'])} |
| Call wall | {gex['call_wall']} | {nq(gex['call_wall'])} |
| Put wall | {gex['put_wall']} | {nq(gex['put_wall'])} |
| Prior day high | {levels['prior_day_high']} | {nq(levels['prior_day_high'])} |
| Prior day low | {levels['prior_day_low']} | {nq(levels['prior_day_low'])} |
| Overnight high | {levels['overnight_high']} | {nq(levels['overnight_high'])} |
| Overnight low | {levels['overnight_low']} | {nq(levels['overnight_low'])} |

## Confluences (across expirations)
{conf_lines}

## Brief
{snap['brief']}

## Trade log
- [ ] Setup taken?
- [ ] Followed the bias or faded it? Why?
- [ ] Outcome + what the P&L was actually driven by (direction / levels / news):
"""
    with open(path, "w") as f:
        f.write(md)
    return path

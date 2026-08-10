# CISD° on SPY — tuning, bugs, and what to change

The indicator was built and calibrated for ES. Most of it transfers, but three
kinds of thing do not: **point-scaled thresholds** (ES trades ~10x SPY's
price), **futures session machinery** (SPY has no 18:00 reopen and no real
Asia/London tape), and **one SMT peer that is the same index as the thing it is
supposed to diverge from**.

Separately, reading it end to end turned up several issues that are not about
SPY at all.

---

## 1. The bug that matters most

### `gateIntrinsic` makes the entire "Institutional Anchor" section a no-op

```pine
intrinsicPath = gateIntrinsic and sweptV > 0.5
anchored = ... (fvgPath or openPath or pdPath or gapPath or amdxPath or intrinsicPath)
```

`sweptV` comes from `f_rb`, which sets `rSwept := 1.0` inside **both** the bull
and bear branches — and those branches only execute when `sweptLo` / `sweptHi`
is already true. So **every RB that forms has `sweptV == 1.0`, always.**

With `gateIntrinsic = true` (the default), `intrinsicPath` is therefore always
true, `anchored` is always true, and the FVG / key-open / deep-P/D gates above
it never bind on anything. Section 4 of the settings appears to be filtering to
institutional anchors and is filtering nothing.

**Fix:** set `gateIntrinsic = false` and let the three real anchors do the work.
Turn it back on only if you find you are getting too few RBs — and know that
when it is on, it is the *only* gate that matters.

**The same fact leaks into the grade.** In `f_grade`:

```pine
s += (sweptV > 0.5 and realSweep) ? wSweep : (sweptV > 0.5 ? wSweep * 0.4 : 0.0)
```

Since `sweptV` is always `1.0`, the `0.0` branch is unreachable and every RB
collects at least `wSweep * 0.4` = 6.4 points. The real base is 24.4, not 18 —
which feeds directly into the saturation in §4. Note that `f_stackCount` uses
`realSweep` rather than `sweptV`, so the **stack count is unaffected and stays
honest**. Another reason to gate on it.

---

## 2. The bias defaults to bullish before it has computed anything

```pine
f_cisdState(int look) =>
    ...
    var int dir = 0
    if not na(dOpen) and dLen >= cisdMinRun and close > dOpen
        dir := 1
    if not na(uOpen) and uLen >= cisdMinRun and close < uOpen
        dir := -1
    dir

biasDir  = request.security(..., biasTf, f_cisdState(cisdBiasLook), ...)
biasBull = biasDir >= 0
```

`dir` starts at `0`, and `biasBull = biasDir >= 0` is **true** for `0`. So until
the first CISD fires on the 60m bias timeframe, the dashboard reads `▲ Bull` and
— with `reqBias = true` — **shorts are gated out and longs pass**, purely from an
uninitialised value.

It is also sticky by design (`var`, never reset to 0), which is defensible for a
bias. The initial state is not.

**Fix:** make neutral explicit rather than bullish.

```pine
biasBull    = biasDir > 0
biasBear    = biasDir < 0
biasNeutral = biasDir == 0
// and in the gate:
biasOk = (not reqBias) or biasNeutral or (z.bull ? biasBull : biasBear) or strongPdLoc
```

That way an uncomputed bias blocks nothing instead of silently blocking one
side. Also worth changing the dashboard row to show `—` when neutral.

---

## 3. A failed gate permanently burns the zone

```pine
if z.armed and not z.tapped and cisdOk
    z.tapped := true
    z.frozen := true
    ... compute gradeOk / biasOk / stackOk / smtGate / dolOk ...
    if gradeOk and biasOk and stackOk and smtGate and dolOk and z.score > barBestScore
```

`tapped` is set **before** the gates are evaluated, so a zone that triggers CISD
but fails any single gate is consumed and can never signal again — even if price
returns later with better confluence.

Whether that is right is a judgement call, but it is worth being deliberate
about, because combined with `maxSignals = 6`, five gates, and the bias bug
above, it is the most likely reason for "why is this barely firing".

**Fix, if you want retests to stay live:**

```pine
if z.armed and not z.tapped and cisdOk
    ... compute gates ...
    if gradeOk and biasOk and stackOk and smtGate and dolOk
        z.tapped := true
        z.frozen := true
        if z.score > barBestScore
            ...
    else
        z.armed := false          // stand down, wait for another approach
```

---

## 4. The grade saturates, so A+ stops meaning anything

Weights sum to roughly:

```
base 18 + PD 22 + sweep 16 + FVG 14 + gap 12 + SMT 12 + open 10
     + AMD 8 + AMDX 18 + DOL 14 + bias 8 + disp 8 + size 4 + tf 5   =  169
```

…against `math.min(s, 100.0)` and an A+ threshold of 80. An ordinary setup —
base, half-depth P/D, a real sweep, an FVG, aligned bias, strong displacement —
already scores ~75, and one more confluence puts it over 80.

So **most anchored setups grade A+**, and `minSignal` barely discriminates
between "A" and "A+".

**What to rely on instead:** `f_stackCount` maxes at 9 and does not saturate. It
is the honest discriminator. Raise `minStack` rather than chasing the letter
grade.

I would *not* start re-tuning the individual weights. With the sample size you
have there is no way to tell tuning from curve-fitting, and the weights are
exactly where that would hide.

---

## 5. Smaller things

| | |
|---|---|
| **CISD level falls back to the RB's own open** | In `f_rb`, `float cisdO = open[o]` is the default when no opposing run is found in `_cisdScan`. The line still draws and still says `CISD°`, but it is not a CISD level. Prefer `na` and skip the draw. |
| **Eviction can kill a live zone** | `if array.size(rbs) > maxRbs` removes index 0 regardless of whether it is active, while dead entries (killed by anti-stack or invalidation) linger in the array. Evict a dead entry first. |
| **RB timeframes below chart TF silently do nothing** | `f_rbGte` requires RB TF ≥ chart TF. On a 15m chart your 5m RB TF is inert with no indication. Add it to the dashboard or leave `dRowRbTf` on while you settle in. |
| **`rbTf3` is computed even when disabled** | `[d3,...] = f_scanRb(rbTf3)` runs unconditionally; `useRb3` only gates intake. Harmless, but it is a `request.security` you are paying for. |
| **`hodRaid` can hold yesterday's high** | On the first bar of a session, `hodPrev` is still the previous day's HOD, so a gap-up sets `hodRaid` to it. That is a PDH raid being reported as an HOD raid. |
| **`invalBuf = 0.0` is tight for SPY** | SPY ticks in 0.01. A single tick through `z.ext` kills the zone. A small buffer stops noise invalidations. |

---

## 6. SPY parameter set

### Point-scaled thresholds

`sweepMode`, `dispMode` and `proxMode` all default to **ATR**, so `sweepPts`,
`dispPts` and `proxPts` are inert unless you switch modes. Change them anyway so
they are not wrong if you ever flip the toggle.

**Two are unconditionally active and both are ES-scaled.** ES ≈ 10 × SPY, so the
conversion is ÷10.

| Input | ES default | **SPY** | Why it matters |
|---|---|---|---|
| `fvgMinPts` | 4.0 | **0.40** | 4.0 on SPY is 0.52%. HTF 60m FVGs on SPY run 0.5–2.0 points, so this silently discards nearly all of them — and `gateFvg` is a primary anchor path. |
| `gapMinPts` | 1.0 | **0.10** | 1.0 is 0.13% on SPY; most opening gaps are smaller and would never register. |
| `sweepPts` | 3.0 | **0.30** | inert in ATR mode |
| `dispPts` | 15.0 | **1.50** | inert in ATR mode |
| `proxPts` | 8.0 | **0.80** | inert in ATR mode |
| `invalBuf` | 0.0 | **0.05** | one tick is 0.01; give it room |

### SMT peers — peer 1 is wrong

`smtSym1 = "CME_MINI:ES1!"` compares SPY against **the same index**. That is not
divergence, it is tracking error (dividends, roll, hours). The peer has to be a
*different* index.

| Input | Change to | Note |
|---|---|---|
| `smtSym1` | `CME_MINI:NQ1!` | Nasdaq vs S&P is the real divergence |
| `smtSym2` | `CBOT_MINI:YM1!` | **verify it resolves** — YM is usually CBOT, not CME |

`request.security(..., ignore_invalid_symbol = true)` means a bad ticker returns
`na` **silently**: `f_smtBear`'s `d2` branch requires `not na(p2Hi1)`, so the peer
just contributes nothing and the dashboard shows `—`. Check the SMT row actually
changes state before trusting it.

Keeping the peers on **futures** is right, incidentally — it gives you something
the NYAM dashboard does not have, since the dashboard's SMT is SPY vs QQQ. Using
QQQ here would duplicate an input you already have.

### Futures-only machinery — turn it off

SPY has no 18:00 CME reopen and effectively no Asia/London tape.

| Input | Set to | Why |
|---|---|---|
| `useO1800` | `false` | no 18:00 bar on SPY |
| `useNDOG` | `false` | keys off the 17→18 futures reopen |
| `useNWOG` | `false` | forms at Sunday 18:00; SPY has no Sunday bar |
| `useOPG` | **`true`** | the 09:30 RTH gap is the real one for SPY |
| `pxAsia` / `pxLon` | `false` | no meaningful SPY liquidity in those windows |
| `huntAsia` / `huntLon` | `false` | same |
| `poolAsia` / `poolLon` | `false` | those ranges never populate, so the sweeps never fire |
| `huntNY` | `true` | keep |
| `kzNYSess` | `0800-1100` | SPY premarket is thin before ~08:00 |
| `amdSess2` | leave or disable | London window is low-information for SPY |

`pxNY`, `poolPD`, `poolHL` and `poolNY` all stay on — those are real for SPY.

### Structure

| Input | Value | Note |
|---|---|---|
| `rbTf1` / `rbTf2` | `5` / `15` | fine for SPY intraday |
| `biasTf` | `60` | fine |
| `gateIntrinsic` | **`false`** | see §1 — this is the important one |
| `minStack` | `3`, raise to `4` once you see the rate | the non-saturating filter |
| `maxSignals` | `6` | fine |

---

## 7. How to use it next to the dashboard

They answer different questions and that is the whole case for running both:

- **NYAM dashboard** — dealer positioning. *Where* hedging pushes price: gamma
  flip, call/put walls, magnet, net GEX, OI shift, flow.
- **CISD°** — order-flow narrative. *When*: sweep → displacement → close back
  through the origin of the opposing series.

The trap is that they overlap more than they look. Both read prior-day high/low,
session ranges, premium/discount against the prior day, and liquidity sweeps. If
both light up it can feel like independent confirmation when it is partly the
same input read twice.

**Treat CISD as a trigger on the dashboard's levels, not as a second opinion
about direction.** The setup worth waiting for is a CISD RB that lands on a
level the dashboard independently cares about — the put wall in positive gamma,
the flip, or something already in the `confluences` panel.

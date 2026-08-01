# CLAUDE.md

**NYAM Terminal** — a desktop trading application for SPY options day trading.
Successor to the NYAM Bias Engine, extended with an OptionsFlow-style flow
scanner and GEX workspace, and a WorldMonitor-style news rail.

Real money is traded off what this shows. That constraint drives every rule below.

## Shape

```
engine/      Python. The math and the data layer. FastAPI over localhost.
src/         TypeScript + React frontend. Everything the user sees.
src-tauri/   Rust. The native shell — window, menus, sidecar lifecycle, packaging.
docs/        Decisions worth not re-deriving.
```

The Python engine runs as a **Tauri sidecar**: the shell spawns it on launch and
kills it on exit. The frontend only ever talks to `http://127.0.0.1:<port>`, so
the engine stays independently runnable (`python -m engine.server`) and
independently testable. Mobile is deferred, but Tauri 2 builds Android from this
same tree when we want it — don't introduce desktop-only assumptions in `src/`.

## Rules

**The math is Python and stays Python.** `engine/analysis/gex.py` is the version
that survived 13 numerical checks tied to four bugs that were live. Do not port
it to TypeScript for stack tidiness. The frontend renders numbers; it does not
compute them. A Greek computed in JavaScript is a Greek nobody verified.

**Run `python engine/verify_gex.py` before and after touching anything in
`engine/analysis/`.** Not ceremony — every check corresponds to a real bug.

**Every new pricing or Greek function needs a finite-difference test against the
analytic form.** Central difference, `h ~ 1e-4..1e-6`. Test call *and* put and
any structural identity (put-call parity; `theta = -1/2 gamma S^2 sigma^2` at
r=q=0). See the `options-math` skill.

**Stdlib-only in the math.** `math.erf` is the only special function this domain
needs. Third-party libraries are fine in the data and presentation layers, but
they import inside the function that needs them so the pricing core stays
importable with zero dependencies.

**The data layer is the only thing that touches the outside world.**
`engine/data/data_sources.py` translates every provider into one market dict.
Everything downstream — GEX math, bias engine, UI — reads only that shape, so
swapping providers never touches the math or the UI.

**Never invent a value to fill a slot.** A missing gamma flip renders as "no
flip in range". A stale feed says it is stale. A value that came from the free
fallback says so. See the six data-integrity rules in the `trading-ui` skill.

**Disagreements are surfaced, not resolved.** Where our computed levels sit
beside Unusual Whales' own, show both and the drift. Two computations that agree
are evidence; one silently overwriting the other is not.

## Conventions

- `T` in **years**. 1 calendar day = 1/365, 1 trading day = 1/252.
- `r`, `q`, `sigma` are decimals, continuously compounded (`0.16` == 16 vol).
- Greeks are **broker-scaled** at the boundary: vega/rho per point (÷100), theta
  per calendar day (÷365). Never scale mid-calculation. Say so in the docstring.
- Dealer sign convention: **long calls, short puts**. An assumption, not a law —
  changing it invalidates every stored track record.
- UI: dark tokens only, from `src/styles/tokens.css`. Six hues, no additions.
  Every number in `--mono` with tabular figures.
- Charts: **Lightweight Charts** (Apache 2.0). TradingView Advanced Charts is
  not licensed for personal use — don't reach for it.

## Data

Unusual Whales is the primary feed (`UW_API_KEY`). **Probe before trusting it** —
tier coverage is not knowable in advance:

```bash
python -m engine.data.unusual_whales probe SPY
```

Anything the probe fails degrades per-endpoint to the free path rather than
breaking the load, and the snapshot records why. See the `unusual-whales` skill.

Trading is manual. **Do not wire this to any execution API.**

## Skills

Three personal skills carry the knowledge this project keeps needing:
`/options-math`, `/unusual-whales`, `/trading-ui`. Read them rather than
re-deriving; update them when a rule here changes.

## Status

Scaffolding. Nothing works yet.

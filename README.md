# NYAM Terminal

A desktop trading terminal for SPY options day trading. Successor to the NYAM
Bias Engine, extended toward an OptionsFlow-style flow scanner and GEX
workspace with a news rail.

Native window (Tauri 2), ~32 MB resident. Not a browser tab.

> Probabilistic lean from positioning data. Not a prediction. Not financial
> advice. Trading is manual — this app is deliberately not wired to any
> execution API.

---

## Run it

```bash
npm run tauri dev
```

That's the whole thing. The shell starts the Python engine itself and kills it
on exit, so there is no second terminal to babysit and no stale process left
holding the port.

Boots in **mock mode** — synthetic but realistic data, zero keys, zero cost —
so the whole board renders before anything is wired live. Every screen says
`MOCK` while it does.

To run the engine alone (tests, debugging, curling the API):

```bash
cd engine && python server.py
```

Then `http://127.0.0.1:8765/api/health`, or `/api/docs` for the full surface.

---

## Shape

```
engine/      Python. The math and the data layer. FastAPI on 127.0.0.1:8765.
src/         TypeScript + React. Everything you see.
src-tauri/   Rust. Window, engine lifecycle, packaging.
```

The frontend renders numbers; it never computes them. A Greek computed in
JavaScript is a Greek nobody verified.

`engine/data/data_sources.py` is the only file that touches the outside world.
Everything downstream reads one market dict, so swapping providers never
touches the math or the UI.

---

## Data

| Mode | Env | What you get |
|---|---|---|
| Mock | `NYAM_MOCK=1` | Offline synthetic data. No keys, no cost. **Default.** |
| Yahoo | `NYAM_MOCK=0 NYAM_PROVIDER=yahoo` | Free, ~15-min delayed chains. No OI change, no flow, no dark pool. |
| Unusual Whales | `NYAM_MOCK=0 NYAM_PROVIDER=uw UW_API_KEY=...` | Real-time chains, day-over-day OI, flow alerts, dark pool prints. |

**Probe the key before trusting it** — endpoint coverage varies by tier and is
not knowable in advance:

```bash
cd engine && python -m data.unusual_whales probe SPY
```

Anything that fails degrades to Yahoo per-endpoint rather than breaking the
load, and the snapshot records why.

`oi_change` is the one input the free path cannot supply — yfinance has no
previous session to diff against, so the OI Shift signal reads a hardcoded `0`
on live data until a UW key is set.

---

## Verifying the math

```bash
cd engine && python verify_gex.py
```

13 numerical checks. Gamma is finite-differenced against an independent
Black-Scholes pricer rather than compared to a hardcoded number; the gamma flip
is verified to be an actual zero of net gamma with a sign change across it; the
regime label is checked against the sign of net GEX; walls are checked to sit on
the correct side of spot.

Every one of those checks corresponds to a bug that was live in this code —
an inverted regime label, a gamma flip returning 421 against a 684 spot, a
"floor" rendering above the current price, an overnight low 5% off a phantom
bar. Run it before and after touching anything in `engine/analysis/`.

---

## What the UI promises

These are enforced, not aspirational:

- **Every panel shows its data age**, and says so when a feed goes stale.
- **No invented values.** A missing gamma flip renders "no flip in range".
- **Modeled vs observed is visually distinct** — dashed levels, solid prices.
- **Disagreements are surfaced, not resolved.** Where our levels sit beside
  Unusual Whales' own, both are shown with the drift. Neither overwrites the
  other.
- **Direction is never carried by colour alone** — always a sign, glyph, or
  position too.
- **The track record admits when it's noise.** Under 30 graded sessions it says
  so, and every rate is stamped with the rule that graded it.

---

## Status

Working: GEX workspace, bias with per-signal reasoning, level map, track
record, engine lifecycle.

Not built yet: flow scanner (needs a UW key), news rail, price charts
(Lightweight Charts), in-app chat, packaged installer.

Mobile is deferred. Tauri 2 builds Android from this same tree when wanted, so
nothing here forecloses it — keep desktop-only assumptions out of `src/`.

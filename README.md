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

## Verifying

Six suites. Run them before and after touching anything in `engine/`.

```bash
cd engine
python verify_gex.py        # 13 — the options math
python verify_bars.py       # 20 — chart bar series
python verify_news.py       # 30 — RSS/Atom parsing and ranking
python verify_news_risk.py  # 24 — news -> bias conviction path
python verify_oi_store.py   # 21 — daily OI snapshots
python verify_ui.py         # payload contract (needs the engine running)
```

`verify_gex.py` finite-differences gamma against an independent Black-Scholes
pricer rather than comparing to a hardcoded number; verifies the gamma flip is
an actual zero of net gamma with a sign change across it; checks the regime
label against the sign of net GEX; checks walls sit on the correct side of spot.
Every one of those corresponds to a bug that was live — an inverted regime
label, a flip returning 421 against a 684 spot, a "floor" above the current
price, an overnight low 5% off a phantom bar.

The newer suites guard the same class of failure — **a value that is unknown
rendering as though it were measured**:

- `verify_news_risk` exists because the News Risk signal was dead. `bias_engine`
  reduced conviction on `high_impact`, and the only producers hardcoded it
  `True` (mock) and `False` (live). It fired exactly never on real data.
- `verify_oi_store` caught that the no-baseline path left `oi_change` absent,
  which `gex.py`'s `.get(..., 0)` would have read as a genuine zero — "we never
  looked" rendering as "positioning didn't shift".
- `verify_ui` asserts no `NaN` reaches the frontend, since JS paints it as the
  literal text `NaN` on a board you size positions from.

Genuinely visual bugs — did the canvas paint, is a panel clipped — need a real
compositing window and can't be faked. Screenshot the app.

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

## Packaging

Two steps, in order — the installer bundles whatever `engine/dist/` contains,
so a stale engine build ships silently if you skip the first:

```bash
cd engine && python -m PyInstaller engine.spec --noconfirm
cd .. && npm run tauri build
```

The engine is built ONEDIR rather than onefile: onefile unpacks its whole tree
to a temp directory on every launch, which adds seconds of startup during which
the app looks hung.

The shell prefers a bundled engine over the source tree and says which it used
in its startup line. That order matters — a packaged install silently falling
through to a developer checkout would run different code than was shipped, and
the only symptom would be numbers that don't match.

Installer lands in `src-tauri/target/release/bundle/`.

## Status

Working: six sections behind a rail (`Ctrl+1`–`6`), price chart with our gamma
levels overlaid, GEX workspace, bias with per-signal reasoning, level map,
track record, news rail over 12 public feeds, event risk feeding the bias,
daily OI snapshots, engine lifecycle, packaged build.

Not built yet: flow scanner (needs a UW key), in-app chat, market-session map.

**Known limits, stated rather than papered over:**

- Event risk is LANDED news, not a forward calendar. It cannot tell you CPI is
  due at 08:30 tomorrow — RSS carries what published, not what is scheduled.
  A real calendar is a separate source and is not wired.
- The US Treasury feed returns 503. It's in the registry and reported as failed
  rather than quietly dropped.
- OI change needs one prior session before it means anything. Day one reports
  `available: false`, not zero.

Mobile is deferred. Tauri 2 builds Android from this same tree when wanted, so
nothing here forecloses it — keep desktop-only assumptions out of `src/`.

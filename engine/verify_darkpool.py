"""
verify_darkpool.py  --  the dark pool summariser and its spread inference.

`lean` is an inference, not a reported fact: dark pool prints carry no
aggressor flag, so side is read from where the print sat between the NBBO bid
and ask. That makes the boundary conditions worth pinning down, because an
inference that quietly fabricates a side on missing or crossed quotes is worse
than one that admits it does not know.

Pure functions on synthetic rows, so this runs offline with no key.
Run:  python verify_darkpool.py
"""
import sys

from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def row(price, bid, ask, **kw):
    r = {"price": str(price), "nbbo_bid": str(bid), "nbbo_ask": str(ask),
         "size": "100", "premium": "75800", "ticker": "SPY",
         "executed_at": "2026-08-03T19:59:00Z", "market_center": "L"}
    r.update(kw)
    return r


print("[0] side is read from position in the spread")
out = uw.summarize_darkpool([
    row(100.00, 100.00, 100.10),   # exactly at bid
    row(100.10, 100.00, 100.10),   # exactly at ask
    row(100.05, 100.00, 100.10),   # midpoint
    row(100.09, 100.00, 100.10),   # near ask
    row(100.01, 100.00, 100.10),   # near bid
])
check("at bid -> sell", out[0]["lean"] == "sell", str(out[0]["lean"]))
check("at ask -> buy", out[1]["lean"] == "buy", str(out[1]["lean"]))
check("midpoint -> mid, not forced to a side", out[2]["lean"] == "mid",
      str(out[2]["lean"]))
check("near ask -> buy", out[3]["lean"] == "buy", str(out[3]["lean"]))
check("near bid -> sell", out[4]["lean"] == "sell", str(out[4]["lean"]))
check("spread_pos 0 at bid", out[0]["spread_pos"] == 0.0, str(out[0]["spread_pos"]))
check("spread_pos 1 at ask", out[1]["spread_pos"] == 1.0, str(out[1]["spread_pos"]))
check("spread_pos 0.5 at mid", abs(out[2]["spread_pos"] - 0.5) < 1e-9,
      str(out[2]["spread_pos"]))

print("[1] a missing or nonsensical NBBO yields no lean, not a guess")
bad = uw.summarize_darkpool([
    row(100.05, 0, 0),                 # no quote at all
    row(100.05, 100.10, 100.00),       # crossed book
    row(100.05, 100.05, 100.05),       # zero-width spread
    row(0, 100.00, 100.10),            # no price
])
for i, why in enumerate(("no quote", "crossed book", "zero-width", "no price")):
    check(f"{why} -> lean is None", bad[i]["lean"] is None, str(bad[i]["lean"]))
    check(f"{why} -> spread_pos is None", bad[i]["spread_pos"] is None,
          str(bad[i]["spread_pos"]))

print("[2] prints outside the NBBO are clamped, not extrapolated")
# A print through the quote should read as maximally aggressive, not as a
# position of 1.4 that the UI would render off the end of the bar.
outside = uw.summarize_darkpool([
    row(100.20, 100.00, 100.10),       # above the ask
    row(99.80, 100.00, 100.10),        # below the bid
])
check("above ask clamps to 1.0", outside[0]["spread_pos"] == 1.0,
      str(outside[0]["spread_pos"]))
check("below bid clamps to 0.0", outside[1]["spread_pos"] == 0.0,
      str(outside[1]["spread_pos"]))
check("above ask still reads buy", outside[0]["lean"] == "buy")
check("below bid still reads sell", outside[1]["lean"] == "sell")

print("[3] cancellations are surfaced, never silently dropped")
# A cancelled print is not a trade. Filtering them would let a tape that is
# mostly cancellations read as conviction.
mixed = uw.summarize_darkpool([row(100.10, 100.00, 100.10, canceled=True),
                               row(100.10, 100.00, 100.10)])
check("cancelled row is kept", len(mixed) == 2, str(len(mixed)))
check("cancelled is flagged", mixed[0]["canceled"] is True)
check("live row is not flagged", mixed[1]["canceled"] is False)

print("[4] limit is respected and ordering preserved")
many = [row(100 + i / 100, 100.00, 100.10) for i in range(40)]
lim = uw.summarize_darkpool(many, limit=5)
check("limit honoured", len(lim) == 5, str(len(lim)))
check("order preserved", lim[0]["price"] == 100.0 and lim[4]["price"] == 100.04,
      f"{lim[0]['price']} .. {lim[4]['price']}")

print("[5] numeric coercion — UW sends numbers as strings")
check("price is a float", isinstance(out[0]["price"], float), type(out[0]["price"]).__name__)
check("size is an int", isinstance(out[0]["size"], int), type(out[0]["size"]).__name__)
check("premium is a float", isinstance(out[0]["premium"], float),
      type(out[0]["premium"]).__name__)
junk = uw.summarize_darkpool([row("abc", "x", "y")])
check("garbage does not raise", len(junk) == 1)
check("garbage yields no lean", junk[0]["lean"] is None, str(junk[0]["lean"]))

print("[6] empty input is empty output")
check("no prints -> no rows", uw.summarize_darkpool([]) == [])

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all dark pool checks passed")

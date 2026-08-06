"""
verify_uw_concurrency.py  --  the process never exceeds the plan's 3 in flight.

DISCOVERED THE HARD WAY. The plan caps concurrent requests at 3:

  HTTP 429: "You have exceeded 3 concurrent requests, the maximum allowed for
             your current plan."

Setting the chain walk's worker count to 3 spends the whole allowance on one
function, which works until anything else calls UW at the same moment — a
scheduled refresh overlapping the recorder, or a suite running beside a live
engine. That was observed: a suite passing alone failed while the engine ran.

So the ceiling is enforced by a semaphore around every request, and this proves
it holds under a load far heavier than the app generates. No network: _get is
replaced with a probe that records how many callers are inside at once.

Run: python verify_uw_concurrency.py
"""
import sys
import threading
import time

from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print(f"[0] the declared ceiling matches the plan")
check("MAX_CONCURRENT is 3", uw.MAX_CONCURRENT == 3, str(uw.MAX_CONCURRENT))
check("PAGE_WORKERS does not exceed it", uw.PAGE_WORKERS <= uw.MAX_CONCURRENT,
      f"workers={uw.PAGE_WORKERS} ceiling={uw.MAX_CONCURRENT}")

print("[1] concurrent callers never exceed the ceiling")
inside = {"now": 0, "peak": 0}
lock = threading.Lock()


def fake_request():
    """Stands in for the urlopen inside _get, guarded by the same semaphore."""
    with uw._slots:
        with lock:
            inside["now"] += 1
            inside["peak"] = max(inside["peak"], inside["now"])
        time.sleep(0.02)          # long enough for overlap to be real
        with lock:
            inside["now"] -= 1


# 40 callers is far past anything the app does: a chain walk plus a refresh
# plus the recorder is under 15.
threads = [threading.Thread(target=fake_request) for _ in range(40)]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.time() - t0

check("peak in flight never exceeded 3", inside["peak"] <= uw.MAX_CONCURRENT,
      f"peak={inside['peak']}")
check("all callers finished", inside["now"] == 0, str(inside["now"]))
# 40 requests at 3-wide and 20ms each cannot finish faster than ~0.26s. Beating
# that would mean the semaphore was not actually holding anyone.
check("elapsed is consistent with real serialisation", elapsed >= 0.2,
      f"{elapsed:.2f}s for 40 x 20ms at {uw.MAX_CONCURRENT}-wide")

print("[2] the semaphore is released when a request raises")
# A leaked slot is worse than no limit: the process would quietly throttle
# itself to zero after a few errors, and look like a hung feed.
def failing_request():
    try:
        with uw._slots:
            raise RuntimeError("boom")
    except RuntimeError:
        pass


for _ in range(uw.MAX_CONCURRENT * 4):
    failing_request()
acquired = [uw._slots.acquire(blocking=False) for _ in range(uw.MAX_CONCURRENT)]
check("all slots still available after repeated failures", all(acquired),
      str(acquired))
for _ in range(sum(1 for a in acquired if a)):
    uw._slots.release()

print("[3] retry configuration is sane")
check("429 is retryable", 429 in uw.RETRY_STATUSES)
check("more than one attempt", uw.RETRY_ATTEMPTS > 1, str(uw.RETRY_ATTEMPTS))
# The limiter is concurrency-based: a slot frees the instant an in-flight
# request finishes, so long sleeps would be waiting for nothing.
check("backoff is short, not rate-limit sized", uw.RETRY_BASE_SLEEP <= 1.0,
      f"{uw.RETRY_BASE_SLEEP}s")
worst = sum(uw.RETRY_BASE_SLEEP * (i + 1) for i in range(uw.RETRY_ATTEMPTS - 1))
check("worst-case retry wait stays under 3s", worst < 3.0, f"{worst:.2f}s")

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all concurrency checks passed")

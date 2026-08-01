"""
verify_sessions.py  --  Checks on the market session clock.

The failure that matters: the clock saying OPEN on a day the market is shut.
Holidays are derived rather than pasted, so these checks pin the derivation
against dates verified independently — including the weekend-observation rule
and Good Friday, which moves every year.

    python verify_sessions.py
"""
import datetime as dt

import config
from analysis import sessions as S

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def at(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=config.TZ)


def main():
    print("[1] Easter and Good Friday")
    # Independently known Easter Sundays.
    for year, month, day in ((2026, 4, 5), (2027, 3, 28), (2024, 3, 31), (2025, 4, 20)):
        got = S.easter(year)
        check(f"Easter {year} == {year}-{month:02d}-{day:02d}",
              got == dt.date(year, month, day), str(got))
    gf = S.holidays(2026)
    check("Good Friday 2026 is 2026-04-03",
          dt.date(2026, 4, 3) in gf and gf[dt.date(2026, 4, 3)] == "Good Friday",
          str([d for d, n in gf.items() if n == "Good Friday"]))

    print("[2] nth-weekday holidays land on the right day")
    h26 = S.holidays(2026)
    expected = {
        dt.date(2026, 1, 19): "Martin Luther King Jr. Day",   # 3rd Mon Jan
        dt.date(2026, 2, 16): "Presidents' Day",              # 3rd Mon Feb
        dt.date(2026, 5, 25): "Memorial Day",                 # last Mon May
        dt.date(2026, 9, 7): "Labor Day",                     # 1st Mon Sep
        dt.date(2026, 11, 26): "Thanksgiving",                # 4th Thu Nov
    }
    for d, name in expected.items():
        check(f"{name} {d}", h26.get(d) == name, str(h26.get(d)))
        check(f"{name} weekday sane", d.weekday() in (0, 3), str(d.weekday()))

    print("[3] weekend observation shifts the right way")
    # Jul 4 2026 is a Saturday -> observed Friday Jul 3.
    check("Jul 4 2026 falls Saturday", dt.date(2026, 7, 4).weekday() == 5)
    check("observed Friday Jul 3", h26.get(dt.date(2026, 7, 3)) == "Independence Day",
          str(h26.get(dt.date(2026, 7, 3))))
    # Jan 1 2027 is a Friday -> no shift.
    check("Jan 1 2027 observed in place",
          S.holidays(2027).get(dt.date(2027, 1, 1)) == "New Year's Day")
    # Dec 25 2027 is a Saturday -> observed Friday Dec 24.
    check("Christmas 2027 observed Dec 24",
          S.holidays(2027).get(dt.date(2027, 12, 24)) == "Christmas Day",
          str(S.holidays(2027).get(dt.date(2027, 12, 24))))

    print("[4] the clock is CLOSED on holidays and weekends")
    st = S.state(at(2026, 11, 26, 10, 30))            # Thanksgiving, 10:30
    check("Thanksgiving not a trading day", st["trading_day"] is False)
    check("reason named", st["closed_reason"] == "Thanksgiving", str(st["closed_reason"]))
    check("phase closed", st["phase"] == "closed", st["phase"])
    check("no US session active",
          not any(s["active"] for s in st["sessions"] if s["kind"] == "us"))

    st = S.state(at(2026, 8, 1, 10, 30))              # Saturday
    check("Saturday not a trading day", st["trading_day"] is False)
    check("weekend reason", st["closed_reason"] == "Weekend", str(st["closed_reason"]))

    print("[5] a normal open day reads open")
    st = S.state(at(2026, 8, 3, 10, 30))              # Monday 10:30
    check("Monday is a trading day", st["trading_day"] is True, str(st["closed_reason"]))
    check("phase open", st["phase"] == "open", st["phase"])
    on = {s["id"] for s in st["sessions"] if s["active"]}
    check("regular session active", "regular" in on, str(on))
    check("pre-market not active at 10:30", "pre" not in on, str(on))
    check("minutes to close is 330", st["minutes_until"] == 330, str(st["minutes_until"]))

    print("[6] named windows fire in the right places")
    def wins(when):
        return {w["id"] for w in S.state(when)["windows"] if w["active"]}
    check("overlap at 10:00", "overlap" in wins(at(2026, 8, 3, 10, 0)))
    check("no overlap at 12:00", "overlap" not in wins(at(2026, 8, 3, 12, 0)))
    check("lunch at 12:00", "lunch" in wins(at(2026, 8, 3, 12, 0)))
    check("power hour at 15:30", "power" in wins(at(2026, 8, 3, 15, 30)))
    check("no windows on a holiday", wins(at(2026, 11, 26, 10, 0)) == set())

    print("[7] pre-market")
    st = S.state(at(2026, 8, 3, 8, 0))
    check("phase pre", st["phase"] == "pre", st["phase"])
    check("counts down to the open", st["minutes_until"] == 90, str(st["minutes_until"]))
    check("pre-market session active",
          any(s["id"] == "pre" and s["active"] for s in st["sessions"]))

    print("[8] the traded window comes from config, not a second copy")
    # If these drift apart, the clock and the grading rule disagree about what
    # was even being measured.
    st = S.state(at(2026, 8, 3, 10, 0))
    check("my_window active at 10:00", st["my_window"]["active"] is True)
    check("my_window end == GRADE_EXIT_TIME",
          st["my_window"]["end"] == config.GRADE_EXIT_TIME,
          f'{st["my_window"]["end"]} vs {config.GRADE_EXIT_TIME}')
    st = S.state(at(2026, 8, 3, 13, 0))
    check("my_window inactive after it", st["my_window"]["active"] is False)

    print("[9] early closes")
    ec = S.early_closes(2026)
    check("day after Thanksgiving 2026 is early",
          ec.get(dt.date(2026, 11, 27)) == "Day after Thanksgiving", str(ec))
    check("Christmas Eve 2026 is early (a Thursday)",
          ec.get(dt.date(2026, 12, 24)) == "Christmas Eve", str(ec.get(dt.date(2026, 12, 24))))
    # Jul 3 2026 is the OBSERVED holiday, so it must not also be an early close.
    check("Jul 3 2026 is a holiday, not an early close",
          dt.date(2026, 7, 3) not in ec, str(dt.date(2026, 7, 3) in ec))
    st = S.state(at(2026, 11, 27, 13, 30))
    check("after a 13:00 close the phase is closed", st["phase"] == "closed", st["phase"])
    check("early close flagged", st["early_close"] == "13:00", str(st["early_close"]))

    print("[10] the Tokyo session wraps midnight correctly")
    def asia(when):
        return next(s["active"] for s in S.state(when)["sessions"] if s["id"] == "asia")
    check("active at 20:00", asia(at(2026, 8, 3, 20, 0)))
    check("active at 01:00", asia(at(2026, 8, 4, 1, 0)))
    check("inactive at 10:00", not asia(at(2026, 8, 3, 10, 0)))

    print("[11] no holiday collides with a weekend after observation")
    for year in (2025, 2026, 2027, 2028):
        bad = [str(d) for d in S.holidays(year) if d.weekday() >= 5]
        check(f"{year} observed holidays are weekdays", not bad, ", ".join(bad))

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
        raise SystemExit(1)
    print("all session checks passed")


if __name__ == "__main__":
    main()

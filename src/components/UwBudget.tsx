import type { UwBudget as Budget } from "../lib/api";
import { int } from "../lib/format";
import { Empty } from "./Panel";

/**
 * Today's Unusual Whales request usage against the 30,000/day cap.
 *
 * Worth a panel rather than a footnote. One full market build measures ~26
 * requests, 28 of which would be the paged option chain if it were not filtered
 * — so a 6.5-hour session at a five-minute refresh costs roughly 2,000, under
 * 7%. That is comfortable, and it stops being comfortable quickly: the cost
 * scales with the ticker count and inversely with the refresh interval, both of
 * which are one settings change away.
 *
 * Counted server-side from actual HTTP calls and persisted across restarts, not
 * projected from the refresh interval. A budget you reason about instead of
 * measuring is one you discover mid-session, with the board frozen.
 */
export function UwBudget({ budget }: { budget: Budget | null | undefined }) {
  if (!budget) {
    return (
      <Empty>
        Not applicable — request budgeting only exists on the Unusual Whales
        provider. Yahoo is unmetered because it is unsupported, not generous.
      </Empty>
    );
  }

  const pct = Math.min(budget.pct, 100);
  const tone = pct >= 90 ? "hot" : pct >= 70 ? "warn" : "";

  return (
    <div className="budget">
      <div className="budget__row">
        <span className="budget__used num">{int(budget.used)}</span>
        <span className="track__base">
          of {int(budget.limit)} · {budget.pct}%
        </span>
      </div>

      <div
        className="budget__bar"
        role="meter"
        aria-valuenow={budget.used}
        aria-valuemin={0}
        aria-valuemax={budget.limit}
        aria-label="Unusual Whales daily request usage"
      >
        <div
          className={`budget__fill${tone ? ` budget__fill--${tone}` : ""}`}
          style={{ width: `${Math.max(pct, pct > 0 ? 1 : 0)}%` }}
        />
      </div>

      <div className="budget__row">
        <span className="track__label">{int(budget.remaining)} left today</span>
        <span className="track__base">resets 00:00 · {budget.date}</span>
      </div>

      <p className="disclaimer">
        Counted from real requests and kept across engine restarts. A full
        refresh of one ticker costs about 26, so a normal session uses well under
        a tenth of this. Adding tickers or shortening the refresh interval moves
        it fast — this is the number that tells you before the cap does.
      </p>
    </div>
  );
}

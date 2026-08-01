import type { Snapshot } from "../lib/api";

/**
 * What the bias engine's News Risk signal is actually reacting to.
 *
 * The signal only ever cuts CONVICTION, never direction — so this panel says
 * that plainly. Showing a risk read without showing what it read would make it
 * unauditable, which is the one thing every other panel here refuses to be.
 *
 * "Unknown" and "none" are rendered differently on purpose. A feed outage
 * means event risk is UNKNOWN; reporting that as a calm tape is the dangerous
 * direction to be wrong in.
 */
export function EventRisk({ news }: { news: Snapshot["news"] }) {
  const level = news.level ?? (news.high_impact ? "high" : "none");
  const unavailable = news.kind === "unavailable" || level === "unknown";

  const tone =
    unavailable ? "warn" : level === "high" ? "down" : level === "medium" ? "warn" : "up";
  const headline =
    unavailable ? "EVENT RISK UNKNOWN"
      : level === "high" ? "HIGH-IMPACT RELEASE"
      : level === "medium" ? "SECONDARY DRIVER"
      : "NO MAJOR RELEASE";

  return (
    <div className="erisk">
      <div className="erisk__head">
        <span className={`chip chip--${tone}`}>{headline}</span>
        {news.window_hours !== undefined && !unavailable && (
          <span className="erisk__window">last {news.window_hours}h</span>
        )}
        {news.kind === "landed" && (
          <span className="erisk__kind" title="Detected from published news, not a scheduled calendar">
            landed news
          </span>
        )}
      </div>

      {news.why && <p className="erisk__why">{news.why}</p>}

      {news.drivers && news.drivers.length > 0 && (
        <table className="levels">
          <tbody>
            {news.drivers.map((d, i) => (
              <tr key={`${d.event}-${i}`}>
                <td className={`levels__tag levels__tag--${d.impact === "high" ? "down" : "watch"}`}>
                  {d.event}
                </td>
                <td className="levels__role">{d.source}</td>
                <td className="num levels__dist">{d.age_hours}h</td>
                <td>
                  {d.primary && <span className="chip chip--quiet">official</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {news.feed_errors && Object.keys(news.feed_errors).length > 0 && (
        <p className="erisk__degraded">
          {Object.keys(news.feed_errors).length} of {news.feed_count} feeds
          unavailable — this read is based on partial coverage.
        </p>
      )}

      <p className="disclaimer">
        Event risk changes conviction, never direction. The lean above is unaffected.
      </p>
    </div>
  );
}

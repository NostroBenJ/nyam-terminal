import type { TrackRecord as Track } from "../lib/api";

/**
 * Does the bias actually work?
 *
 * Directional accuracy against a 50% baseline is the only number here that
 * means anything, so it leads and the rest is context. Two honesty rules the
 * panel enforces rather than assumes:
 *  - under ~30 graded days it says the sample is noise, because it is;
 *  - the grading rule is stamped on screen, so a later change to the window
 *    or the band can't quietly blend incomparable results into one rate.
 */

const MIN_MEANINGFUL = 30;

export function TrackRecord({ track }: { track: Track }) {
  if (!track || track.n === 0) {
    return <p className="empty">No graded calls yet for {track?.ticker ?? "this ticker"}.</p>;
  }

  // A graded NEUTRAL call counts toward `n` but not toward `dir_n`, so a book
  // of only neutral calls passes the n===0 guard above with no directional
  // rate to show. dir_hit_rate is null there — correctly, since no such rate
  // exists — and treating it as a number rendered "NaN%" at best and threw at
  // worst. There is nothing to display, so display that.
  const rated = track.dir_hit_rate !== null && track.dir_n > 0;
  const edge = rated ? (track.dir_hit_rate as number) - 50 : 0;
  const thin = track.dir_n < MIN_MEANINGFUL;
  const cls = !rated || thin ? "flat" : edge > 0 ? "up" : edge < 0 ? "down" : "flat";

  return (
    <div className="track">
      <div className="track__head">
        <span className={`track__rate num track__rate--${cls}`}>
          {rated ? `${(track.dir_hit_rate as number).toFixed(0)}%` : "—"}
        </span>
        <div className="track__meta">
          <span className="track__label">directional accuracy</span>
          <span className="track__base">
            {track.dir_n} graded · baseline 50%
            {rated && (
              <>
                {" · "}
                <span className={`num track__edge track__edge--${cls}`}>
                  {edge > 0 ? "+" : edge < 0 ? "−" : "±"}
                  {Math.abs(edge).toFixed(0)} pts
                </span>
              </>
            )}
          </span>
        </div>
      </div>

      {!rated && (
        <p className="track__warn">
          No directional calls graded yet — {track.n} call
          {track.n === 1 ? "" : "s"} so far, all neutral. A neutral call is
          graded but has no direction to be right about, so there is no
          accuracy to report rather than an accuracy of zero.
        </p>
      )}

      {rated && thin && (
        <p className="track__warn">
          Sample is too small to mean anything. {MIN_MEANINGFUL - track.dir_n} more graded
          sessions before this number is worth reading.
        </p>
      )}

      <table className="track__types">
        <thead>
          <tr>
            <th>Call</th>
            <th className="num">n</th>
            <th className="num">wins</th>
            <th className="num">rate</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(track.by_type).map(([type, t]) => (
            <tr key={type}>
              <td>{type}</td>
              <td className="num">{t.n}</td>
              <td className="num">{t.wins}</td>
              <td className="num">{t.rate}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="track__strip" title="Most recent graded calls, oldest left">
        {track.recent.map((r) => (
          <i
            key={r.date}
            className={`track__tick track__tick--${r.correct ? "win" : "loss"}`}
            title={`${r.date} · ${r.bias} · predicted ${r.predicted}, actual ${r.actual} (${r.move_pct > 0 ? "+" : ""}${r.move_pct}%)`}
          />
        ))}
      </div>

      <p className="track__rule">
        graded <code>{track.rule}</code>
        {track.pending > 0 && ` · ${track.pending} pending`}
        {track.mixed_rules && (
          <span className="track__mixed"> · mixed rules in history, rates not comparable</span>
        )}
      </p>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api, type CaptureStatus as Status } from "../lib/api";
import { Empty } from "./Panel";

/**
 * What the headless recorder has stored — and what it missed.
 *
 * The missing-days list is the important half. An option chain not captured on
 * a given day cannot be bought back at any price, and a Unusual Whales trial
 * takes its flow and dark pool data with it when it expires. A gap here is a
 * permanent hole in the dataset, so it is shown in red rather than folded into
 * a total.
 */
export function CaptureStatus() {
  const [s, setS] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setS(await api.capture());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !s) return <Empty>Capture status unavailable — {error}</Empty>;
  if (!s) return <Empty>…</Empty>;

  const mb = s.total_bytes / 1024 / 1024;
  const withUw = s.days.filter((d) => d.uw_key).length;

  return (
    <div className="cap">
      <div className="cap__head">
        <span className="cap__stat num">{s.days.length}</span>
        <div className="cap__meta">
          <span className="track__label">days captured</span>
          <span className="track__base">
            {mb < 0.1 ? `${(s.total_bytes / 1024).toFixed(0)} KB` : `${mb.toFixed(1)} MB`}
            {withUw > 0 && ` · ${withUw} with a UW key`}
          </span>
        </div>
      </div>

      {s.missing_trading_days.length > 0 && (
        <div className="cap__missing">
          <strong>{s.missing_trading_days.length} trading day
          {s.missing_trading_days.length === 1 ? "" : "s"} missed</strong> —{" "}
          {s.missing_trading_days.slice(0, 6).join(", ")}
          {s.missing_trading_days.length > 6 && " …"}
          <div className="cap__missingwhy">
            Option chains aren’t retrievable for a past date. These days are gone.
          </div>
        </div>
      )}

      {s.days.length === 0 ? (
        <Empty>
          Nothing captured yet. The recorder runs headlessly — it does not need
          this app open, but the machine has to be on or asleep. Register it with{" "}
          <code>setup-capture-schedule.ps1</code> in the app folder.
        </Empty>
      ) : (
        <table className="levels cap__table">
          <thead>
            <tr>
              <th>date</th>
              <th>tickers</th>
              <th className="num">files</th>
              <th className="num">size</th>
              <th>src</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {s.days.map((d) => {
              const bad =
                Object.keys(d.errors).length > 0 ||
                Object.keys(d.ticker_errors).length > 0;
              return (
                <tr key={d.date}>
                  <td className="num levels__price">{d.date}</td>
                  <td className="levels__role">{d.tickers.join(", ") || "—"}</td>
                  <td className="num">{d.files}</td>
                  <td className="num levels__dist">{(d.bytes / 1024).toFixed(0)} KB</td>
                  <td className="levels__role">
                    {d.provider}
                    {d.uw_key && <span className="chip chip--up cap__uw">UW</span>}
                  </td>
                  <td>
                    {d.trading_day === false ? (
                      <span className="cap__flag cap__flag--dim">non-trading</span>
                    ) : bad ? (
                      <span className="cap__flag cap__flag--bad">errors</span>
                    ) : (
                      <span className="cap__flag cap__flag--ok">ok</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <p className="disclaimer">
        Stores the computed snapshot <em>and</em> the raw option chain, so the
        math can be redone later against real data — verified by replaying a
        captured day and reproducing its levels exactly.
      </p>
    </div>
  );
}

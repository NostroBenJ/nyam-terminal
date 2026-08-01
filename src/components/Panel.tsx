import type { ReactNode } from "react";
import type { Freshness } from "../lib/format";

/**
 * Panel shell.
 *
 * Every panel carries its own data-age chip. That is not decoration: this UI
 * is used to place real trades, and a panel that keeps rendering its last
 * value after a feed drops is the failure mode that matters most.
 */
export function Panel({
  title,
  subtitle,
  freshness,
  age,
  right,
  children,
  grow,
}: {
  title: string;
  subtitle?: string;
  freshness?: Freshness;
  age?: string;
  right?: ReactNode;
  children: ReactNode;
  grow?: boolean;
}) {
  return (
    <section className={`panel${grow ? " panel--grow" : ""}`}>
      <header className="panel__head">
        <div className="panel__titles">
          <h2 className="panel-title">{title}</h2>
          {subtitle && <span className="panel__sub">{subtitle}</span>}
        </div>
        <div className="panel__right">
          {right}
          {freshness && (
            <span className={`age age--${freshness}`} title={age}>
              <i className="age__dot" />
              {freshness === "unknown" ? "age unknown" : age}
            </span>
          )}
        </div>
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

/** Explicit empty state. Never render a plausible-looking placeholder number. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

import { useEffect, useRef, useState } from "react";

/**
 * A number that tints briefly when it changes.
 *
 * This is the one animation a trading screen earns. You need to READ a price,
 * not watch it arrive — so nothing moves, slides, or counts up. A short
 * background tint lets a change register peripherally while you're looking
 * somewhere else, then gets out of the way.
 *
 * Direction is carried by the tint AND by the arrow, never by colour alone.
 * Honours prefers-reduced-motion by skipping the tint entirely; the value
 * still updates, because suppressing the data would be a different thing from
 * suppressing the animation.
 */

const HOLD_MS = 700;

export function Flash({
  value,
  format,
  className = "",
  showArrow = true,
}: {
  value: number | null | undefined;
  format: (v: number | null | undefined) => string;
  className?: string;
  showArrow?: boolean;
}) {
  const prev = useRef<number | null | undefined>(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    const before = prev.current;
    prev.current = value;
    if (
      typeof before !== "number" ||
      typeof value !== "number" ||
      before === value ||
      !Number.isFinite(before) ||
      !Number.isFinite(value)
    ) {
      return;
    }
    setDir(value > before ? "up" : "down");
    const t = setTimeout(() => setDir(null), HOLD_MS);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <span className={`flash${dir ? ` flash--${dir}` : ""} ${className}`}>
      {format(value)}
      {showArrow && dir && (
        <i className={`flash__arrow flash__arrow--${dir}`} aria-hidden="true">
          {dir === "up" ? "▲" : "▼"}
        </i>
      )}
    </span>
  );
}

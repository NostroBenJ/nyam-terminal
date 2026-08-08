import type { Gex } from "../lib/api";
import { price } from "../lib/format";

/**
 * Where price sits between the walls, drawn as an engraved instrument scale.
 *
 * THE POINT. Three numbers you subtract in your head become one position you
 * see. The hatched region below the gamma flip is the fact that actually
 * changes how you trade — hedging AMPLIFIES there instead of fading — and as a
 * texture it reads before you've finished reading anything else.
 *
 * HONESTY IS THE HARD PART, and it is designed in rather than added later. A
 * gauge implies precision, so every case where the precision isn't real has to
 * degrade to something truthful:
 *
 *   - no walls at all      -> no gauge; a line of text saying so
 *   - one wall missing     -> the band is built from what exists and the open
 *                             end is drawn open, never closed at a fake bound
 *   - price outside band   -> the needle pins to the edge AND says "above" /
 *                             "below" in words, because a pinned needle at 100%
 *                             looks identical to price sitting exactly on the
 *                             wall, which is a completely different situation
 *   - no flip              -> no hatching. An unhatched scale means "we don't
 *                             know where the regime line is", not "positive
 *                             gamma everywhere"
 *
 * That list is the whole reason this is a component and not markup.
 */
export function Gauge({ gex }: { gex: Gex }) {
  const { spot, put_wall: lo, call_wall: hi, gamma_flip: flip } = gex;

  // Both walls are needed for a bounded scale. With neither there is no band
  // to draw and inventing one would be inventing the levels themselves.
  if (lo === null && hi === null) {
    return (
      <div className="gauge gauge--none">
        No walls in range — nothing to place price between. The level map has
        whatever levels did resolve.
      </div>
    );
  }

  // A single wall still gives a useful reading: the distance to the one bound
  // that exists. The other end is drawn open so it cannot read as a bound.
  const openLow = lo === null;
  const openHigh = hi === null;
  const span = 0.02; // fabricate 2% of runway on the open side, marked as open
  const from = lo ?? (hi as number) * (1 - span);
  const to = hi ?? (lo as number) * (1 + span);
  const width = to - from;
  if (!(width > 0)) {
    return (
      <div className="gauge gauge--none">
        Walls are crossed or equal ({price(from)} / {price(to)}) — the band is
        not drawable. Treat the level map as the source of truth.
      </div>
    );
  }

  const pct = (v: number) => ((v - from) / width) * 100;
  const clamp = (n: number) => Math.min(100, Math.max(0, n));

  const rawSpot = pct(spot);
  const above = rawSpot > 100;
  const below = rawSpot < 0;
  const outside = above || below;

  // THE FLIP MARKER IS NOT CLAMPED, and that distinction is the whole point.
  //
  // Clamping put a line labelled FLIP exactly on the put wall whenever the
  // flip sat below the band — caught by the probe with flip 740 against a put
  // wall at 763, rendering at 0%. A viewer reads that as "the regime line is
  // the floor", which is a different market from one where the flip is
  // twenty-three points lower. Inventing a level's POSITION is the same fault
  // as inventing its value.
  //
  // So the line is drawn only when the flip is genuinely inside the band; when
  // it exists but sits outside, it is named in the footer with its real price
  // instead, the way the chart pushes off-frame levels to the edge.
  const flipInBand = flip !== null && flip >= from && flip <= to;
  const flipPct = flipInBand ? pct(flip as number) : null;

  // The hatch, by contrast, IS clamped, and correctly: a flip below the band
  // means none of the visible range is amplifying (0%), and a flip above it
  // means all of it is (100%). Both are true statements about the band.
  const hatchTo = flip === null ? null : clamp(pct(flip));
  const flipOutside = flip !== null && !flipInBand;

  const ticks = [0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100];

  return (
    <div className="gauge">
      <div className="gauge__scale">
        {hatchTo !== null && hatchTo > 0 && (
          <div
            className="gauge__shoal"
            style={{ width: `${hatchTo}%` }}
            title="Below the gamma flip — dealer hedging amplifies moves instead of fading them"
          />
        )}

        {ticks.map((t) => (
          <i
            key={t}
            className={`gauge__tick${t % 25 === 0 ? " gauge__tick--maj" : ""}`}
            style={{ left: `calc(${t}% - ${t === 100 ? 1 : 0}px)` }}
          />
        ))}

        {!openLow && (
          <span className="gauge__lv gauge__lv--floor" style={{ left: 0 }}>
            <em>put wall</em>
          </span>
        )}
        {flipPct !== null && (
          <span className="gauge__lv gauge__lv--flip" style={{ left: `${flipPct}%` }}>
            <em>flip</em>
          </span>
        )}
        {!openHigh && (
          <span className="gauge__lv gauge__lv--ceil" style={{ left: "calc(100% - 1px)" }}>
            <em>call wall</em>
          </span>
        )}

        {/* The needle is the only copper on the board. */}
        <span
          className={`gauge__needle${outside ? " gauge__needle--pinned" : ""}`}
          style={{ left: `${clamp(rawSpot)}%` }}
        >
          <b>{price(spot)}</b>
        </span>
      </div>

      <div className="gauge__foot">
        <span className={openLow ? "gauge__open" : ""}>
          {openLow ? "no put wall — open below" : price(from)}
        </span>
        {/* The legend describes what is ACTUALLY on the scale. It used to say
            "hatched = hedging amplifies" while rendering no hatching at all,
            which is a caption for a thing that is not there. */}
        <span className="gauge__legend">
          {hatchTo === null
            ? "no flip in range — regime boundary unknown"
            : hatchTo <= 0
              ? `flip ${price(flip as number)} sits below this band — all of it fades`
              : hatchTo >= 100
                ? `flip ${price(flip as number)} sits above this band — all of it amplifies`
                : "hatched = hedging amplifies"}
        </span>
        <span className={openHigh ? "gauge__open" : ""}>
          {openHigh ? "no call wall — open above" : price(to)}
        </span>
      </div>

      {/* A pinned needle at the edge is visually identical to price sitting
          exactly on the wall. Those are different facts, so one of them says so
          in words. */}
      {outside && (
        <p className="gauge__outside">
          Price is <strong>{above ? "above the call wall" : "below the put wall"}</strong> —
          the needle is pinned at the edge, not sitting on the level.
        </p>
      )}

      {/* Named rather than drawn, because drawing it would put a line labelled
          FLIP on top of a wall it is nowhere near. */}
      {flipOutside && (
        <p className="gauge__outside">
          Gamma flip at <strong>{price(flip as number)}</strong> is outside this
          band, so it has no position on the scale.
        </p>
      )}
    </div>
  );
}

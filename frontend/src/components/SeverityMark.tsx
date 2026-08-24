import { SEVERITY, type SeverityLevel } from "@/design/generated/tokens";

/**
 * The shape channel — §E9.4 rule 2.
 *
 * > **Colour is never the only channel.** Every severity carries a shape and a
 * > label. Colour-blind readers work; grayscale printouts work, and §E19.7
 * > establishes that officers print.
 *
 * The contrast suite measured why this is load-bearing rather than
 * belt-and-braces: `high` and `medium` sit **1.4% apart in grayscale**. On the
 * printouts officers actually make, the mark is not a redundancy — it is the
 * only channel that survives. Which is also why `circle-filled` and
 * `circle-hollow` differ in *fill* and not merely in outline weight.
 *
 * The `switch` is exhaustive against the shapes declared in `tokens.json`. Add
 * a severity level with a new shape and this stops compiling, which is the
 * intended way to find out that a mark needs drawing.
 */

type Shape = (typeof SEVERITY)[SeverityLevel]["shape"];

export function SeverityMark({ level, size = 12 }: { level: SeverityLevel; size?: number }) {
  const shape: Shape = SEVERITY[level].shape;

  return (
    <svg
      className="severity-mark"
      width={size}
      height={size}
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      {mark(shape)}
    </svg>
  );
}

function mark(shape: Shape) {
  switch (shape) {
    case "diamond-filled":
      return <path d="M6 0.6 11.4 6 6 11.4 0.6 6Z" fill="currentColor" />;
    case "circle-filled":
      return <circle cx="6" cy="6" r="5" fill="currentColor" />;
    case "circle-hollow":
      // Hollow, not outlined-thin: the fill is the difference a photocopier
      // keeps and a hairline is the difference it loses.
      return <circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" strokeWidth="2" />;
    case "dot":
      return <circle cx="6" cy="6" r="2.4" fill="currentColor" />;
    case "check":
      return (
        <path
          d="M1.6 6.4 4.6 9.4 10.4 2.8"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="square"
        />
      );
  }
}

import type { PublishedFigure } from "@/public/figures";

// ADR-0021, and M6's gate. `readZone` converts every count into a
// `PublishedFigure` precisely so the raw number cannot be interpolated: a
// suppressed place carries `total_reports: 0` from the backend, and a surface
// that rendered it would publish "0 potholes" about a ward with four.
//
// This is the shape of that mistake, and it must not compile. The only way to
// put a figure on a screen is `<Figure>`, which knows about suppression.
export function Careless({ total }: { readonly total: PublishedFigure }) {
  return <span>{total}</span>;
}

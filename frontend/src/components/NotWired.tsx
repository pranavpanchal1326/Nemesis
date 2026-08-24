import { t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * The "not wired" chip — §E24.
 *
 * > Screens whose contract returns nulls today carry a permanent dev-only badge
 * > and **cannot be routed to a public URL** until the backing phase populates
 * > them. Track E races ahead of the backend without ever lying about it — §6
 * > Principle #8 enforced by the build, not by discipline.
 *
 * The distinction §E1 insists on, and that an earlier draft got wrong: **"not
 * wired" does not mean "no contract".** `work_orders`, `budget_allocations`,
 * `contractors` and the severity fields all exist and return null. These
 * screens are built against the real generated types with fixture *values*,
 * never a fixture *shape* — which is a materially different and much safer
 * position than mocking.
 *
 * Rendering nothing in production is deliberate: the chip is a note to the team,
 * not a disclaimer to a citizen. The thing that protects the citizen is
 * `devOnly()` on the route, which returns 404.
 */
export function NotWired({
  phase,
  strings,
}: {
  /** Which phase populates this contract. Named, so the chip is a pointer. */
  readonly phase: string;
  readonly strings: Strings;
}) {
  if (process.env.NODE_ENV === "production") return null;

  return (
    <span className="not-wired" title={t(strings, "notWired.detail")}>
      <span className="type-micro">{t(strings, "notWired.label")}</span>
      <span className="not-wired__phase type-mono-data">{phase}</span>
    </span>
  );
}

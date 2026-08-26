import { NotWired } from "@/components/NotWired";
import { t, type Strings, type Translated } from "@/lib/i18n/strings";
import "./roadmap.css";

/**
 * The two honesty primitives every ROADMAP screen uses — §E24, §E1, §E3.3.
 *
 * F7's nine screens are built *"against generated types with fixture values,
 * behind the not-wired chip"*, and §E1 is careful about what that does and does
 * not mean:
 *
 * > **"not wired" does not mean "no contract".** `work_orders`,
 * > `budget_allocations`, `contractors` and the severity fields all exist and
 * > return null. These screens are built against the real generated types with
 * > fixture *values*, never a fixture *shape*.
 *
 * There turned out to be a third case the plan did not name, and it is the
 * reason `<ContractGap>` exists. Some of what §E19 describes has **no shape in
 * `openapi.json` at all** — there is no `WorkOrder` response schema, no rate
 * card, no SLA deadline, no integrity signal. For those, a fixture would not be
 * "the real type with invented values"; it would be an invented type, which is
 * precisely what `check-guards.ts`' fourth ban exists to stop and what
 * execution-plan Law 2 forbids.
 *
 * So a roadmap screen renders three kinds of thing, and says which is which:
 *
 *   · fields with a generated type — real shape, fixture values, chip on top;
 *   · fields with a generated **enum** but no container — the enum is rendered
 *     from `generated/enums.ts`, which is a real closed set;
 *   · everything else — named by `<ContractGap>` and not drawn.
 *
 * A screen that drew the third kind anyway would look the most finished and be
 * the least true, which is the trade this whole product is written against.
 */
export function FixtureNotice({
  phase,
  strings,
}: {
  readonly phase: string;
  readonly strings: Strings;
}) {
  return (
    <aside className="fixture" role="note">
      <p className="fixture__title type-micro">
        <NotWired phase={phase} strings={strings} />
        {t(strings, "fixture.title")}
      </p>
      <p className="type-caption">{t(strings, "fixture.body", { phase })}</p>
    </aside>
  );
}

/**
 * A part of §E19 with no contract behind it at all.
 *
 * Rendered as a named absence rather than as an empty panel, because an empty
 * panel is indistinguishable from a bug and a named absence is a work item.
 */
export function ContractGap({
  what,
  strings,
}: {
  /** What is missing, in words — *"a work-order response schema"*, not a
   *  variable name. Passed as `Translated` so it comes from the bundle. */
  readonly what: Translated;
  readonly strings: Strings;
}) {
  return (
    <p className="fixture__gap type-caption">
      <span className="type-micro">{t(strings, "fixture.gap", { what })}</span>
      {t(strings, "fixture.gapWhy")}
    </p>
  );
}

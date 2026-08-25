import { notTranslatable, t, type Strings, type Translated } from "@/lib/i18n/strings";
import {
  HONESTY_COUNTS,
  SURFACE_CLAIMS,
  SYSTEM_CLAIMS,
  type HonestyStatus,
  type SurfaceClaim,
  type SystemClaim,
} from "./generated/honesty";
import "./public.css";

/**
 * §44 and §E28, on a public URL — §E16.2, §E18.
 *
 * > Act 9 renders §44 on the marketing surface. **Every competitor overclaims**;
 * > §6 Principle #8 says this is a competitive advantage rather than a
 * > limitation, and Act 9 is where that belief is actually tested in public.
 *
 * §E18 adds the form: *"published here as data, not as prose."* So the rows come
 * from `generated/honesty.ts`, which `scripts/generate-honesty.ts` writes from
 * the two blueprints and CI drift-checks. Nothing on this screen is typed by
 * hand, which is the only version of this page that stays true.
 *
 * **Status is never colour alone.** §E9.4 rule 2 applies here as much as to
 * severity: the label is the word, in the data face, and the row carries a
 * `data-status` attribute the stylesheet uses for weight and rule — not a green
 * dot that a printed page or a colour-blind reader loses. This table will be
 * printed by somebody arguing with us, and it has to survive that.
 *
 * **Severity ink is not used here at all** (§E9.4 rule 1). ROADMAP is not
 * urgent, REAL is not resolved, and borrowing the severity palette to say so
 * would be the second meaning §E3.4's audit exists to catch.
 */
export function HonestyTable({ strings }: { readonly strings: Strings }) {
  return (
    <div className="honesty">
      <p className="honesty__lede type-body">{t(strings, "honesty.lede")}</p>
      <p className="honesty__why type-caption">{t(strings, "honesty.why")}</p>

      <Legend strings={strings} />

      <section className="honesty__group" aria-labelledby="honesty-surfaces">
        <h2 id="honesty-surfaces" className="type-heading">
          {t(strings, "honesty.rows.frontend")}
        </h2>
        {/*
         * A table, and genuinely a table: these are rows of comparable cells
         * against shared column headers, which is the one shape `<table>` is
         * right for. `<dl>` was right for a place's four figures and is wrong
         * here, and the difference is whether a reader compares down a column.
         */}
        <div className="honesty__scroll">
          <table className="honesty__table">
            <caption className="type-caption">{t(strings, "honesty.rows.frontend")}</caption>
            <thead>
              <tr>
                <th scope="col">{t(strings, "honesty.capability")}</th>
                <th scope="col">{t(strings, "honesty.component")}</th>
                <th scope="col">{t(strings, "honesty.data")}</th>
                <th scope="col">{t(strings, "honesty.closes")}</th>
              </tr>
            </thead>
            <tbody>
              {SURFACE_CLAIMS.map((claim: SurfaceClaim) => (
                <tr
                  key={claim.capability}
                  data-component={claim.component}
                  data-data={claim.data}
                  data-finished={claim.component === "REAL" && claim.data === "REAL"}
                >
                  <th scope="row" className="type-caption">
                    {notTranslatable(claim.capability)}
                  </th>
                  <td>
                    <StatusCell
                      status={claim.component}
                      note={claim.componentNote}
                      strings={strings}
                    />
                  </td>
                  <td>
                    <StatusCell status={claim.data} note={claim.dataNote} strings={strings} />
                  </td>
                  <td className="type-mono-data">{notTranslatable(claim.closesAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="honesty__group" aria-labelledby="honesty-system">
        <h2 id="honesty-system" className="type-heading">
          {t(strings, "honesty.rows.system")}
        </h2>
        <div className="honesty__scroll">
          <table className="honesty__table">
            <caption className="type-caption">{t(strings, "honesty.rows.system")}</caption>
            <thead>
              <tr>
                <th scope="col">{t(strings, "honesty.capability")}</th>
                <th scope="col">{t(strings, "honesty.component")}</th>
                <th scope="col">{t(strings, "honesty.note")}</th>
              </tr>
            </thead>
            <tbody>
              {SYSTEM_CLAIMS.map((claim: SystemClaim) => (
                <tr key={claim.capability} data-component={claim.status}>
                  <th scope="row" className="type-caption">
                    {notTranslatable(claim.capability)}
                  </th>
                  <td>
                    <StatusCell status={claim.status} note={claim.note} strings={strings} />
                  </td>
                  <td className="type-caption">{notTranslatable(claim.why)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/**
 * The label, and the caveat beside it.
 *
 * The caveat is not a footnote. *"REAL (demo-scale, 3 seeded accounts)"* and
 * *"REAL"* are different claims, and a table that rendered the first as the
 * second would be overclaiming in exactly the way this page exists to refuse.
 */
function StatusCell({
  status,
  note,
  strings,
}: {
  readonly status: HonestyStatus | null;
  readonly note: string;
  readonly strings: Strings;
}) {
  return (
    <span className="honesty__status" data-status={status ?? "none"}>
      <span className="honesty__label type-mono-data">{label(status, strings)}</span>
      {note === "" ? null : (
        <span className="honesty__note type-caption">{notTranslatable(note)}</span>
      )}
    </span>
  );
}

function label(status: HonestyStatus | null, strings: Strings): Translated {
  // Exhaustive by construction: `HonestyStatus` is a generated union, so adding
  // a label to the blueprints and regenerating makes this switch fail to
  // compile until somebody decides what the new word is in every locale.
  switch (status) {
    case "REAL":
      return t(strings, "honesty.status.real");
    case "SIMULATED":
      return t(strings, "honesty.status.simulated");
    case "ROADMAP":
      return t(strings, "honesty.status.roadmap");
    case "CUT":
      return t(strings, "honesty.status.cut");
    case "REFRAMED":
      return t(strings, "honesty.status.reframed");
    case null:
      return t(strings, "honesty.status.none");
  }
}

function Legend({ strings }: { readonly strings: Strings }) {
  return (
    <dl className="honesty__legend">
      <div>
        <dt className="type-mono-data">{t(strings, "honesty.status.real")}</dt>
        <dd className="type-caption">{t(strings, "honesty.legendReal")}</dd>
      </div>
      <div>
        <dt className="type-mono-data">{t(strings, "honesty.status.simulated")}</dt>
        <dd className="type-caption">{t(strings, "honesty.legendSimulated")}</dd>
      </div>
      <div>
        <dt className="type-mono-data">{t(strings, "honesty.status.roadmap")}</dt>
        <dd className="type-caption">{t(strings, "honesty.legendRoadmap")}</dd>
      </div>
      <div>
        <dt className="type-mono-data">{t(strings, "honesty.status.cut")}</dt>
        <dd className="type-caption">{t(strings, "honesty.legendCut")}</dd>
      </div>
      <div>
        <dt className="type-mono-data">{t(strings, "honesty.status.reframed")}</dt>
        <dd className="type-caption">{t(strings, "honesty.legendReframed")}</dd>
      </div>
    </dl>
  );
}

/** Published beside the table: how many rows, and how many are finished. A
 *  count a reader can check against the rows is harder to quietly improve. */
export function honestyCounts() {
  return HONESTY_COUNTS;
}

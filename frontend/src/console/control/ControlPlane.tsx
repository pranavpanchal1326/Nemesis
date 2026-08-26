import { formatLedgerTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import type { ControlPlaneData } from "@/server/control-data";

import { ConsolePrint } from "../ConsoleShell";
import { PublicationControl } from "./PublicationControl";
import { TaxonomyForm } from "./TaxonomyForm";
import { TenantForm } from "./TenantForm";
import "./control.css";

/**
 * The control plane, as a surface — §E19, §E14.4, ADR-0046. **REAL**: 35
 * control-plane paths are shipped.
 *
 * F6's gate is Phase 5's own gate re-run through a screen:
 *
 * > a tenant is provisioned, given an invented taxonomy, and published —
 * > entirely through the UI, no SQL, no code change.
 *
 * So the three writes that gate names are here as forms, and everything else is
 * a read that makes the writes checkable. A screen that could provision but not
 * show the result would let the gate pass on a form that submitted into
 * nothing.
 *
 * **The publication control is the one worth arguing about.** ADR-0046 decided
 * that publishing a city's data is *an act somebody takes*, logged as an
 * `admin_action` with a required justification and revocable through the same
 * door. F6 says the justification field must be *"a first-class input rather
 * than a hidden parameter"*, and the reason is not ceremony: a required text
 * field at the moment of the decision is the difference between a log entry
 * that says who, and a log entry that says why. See `<PublicationControl>`.
 *
 * **Each panel fails alone.** `fetchControlPlane` returns `null` per panel
 * rather than throwing, and every panel below renders its own unavailable
 * state. An admin surface is opened *during* incidents, and one dead endpoint
 * must not take the other seven with it.
 */
export function ControlPlane({
  data,
  strings,
  locale,
}: {
  readonly data: ControlPlaneData;
  readonly strings: Strings;
  readonly locale: string;
}) {
  return (
    <div className="control">
      <ConsolePrint title={t(strings, "control.taxonomy")}>
        {data.taxonomy === null ? (
          <Unavailable strings={strings} />
        ) : (
          <>
            <p className="type-mono-data">
              {t(strings, "control.taxonomy.revision", {
                revision: data.taxonomy.revision,
                hash: data.taxonomy.content_hash.slice(0, 12),
              })}
            </p>
            {/*
             * The tree, drawn by indentation from the node's own `depth`. Not
             * a nested `<ul>` built by grouping on `parent_key`: the server
             * already ordered the list by path and computed the depth, and
             * rebuilding the hierarchy here would be a second implementation
             * of the tree that can disagree with the one the classifier reads.
             */}
            <ul className="control__tree">
              {data.taxonomy.nodes.map((node) => (
                <li
                  key={node.key}
                  className="control__node"
                  style={{ "--depth": node.depth } as React.CSSProperties}
                >
                  <span className="type-caption">{notTranslatable(node.display_name)}</span>
                  <span className="control__key type-mono-data">{notTranslatable(node.key)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        <TaxonomyForm strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.zones")}>
        {data.zones === null ? (
          <Unavailable strings={strings} />
        ) : data.zones.length === 0 ? (
          <p className="type-caption">{t(strings, "control.empty")}</p>
        ) : (
          <ul className="control__rows">
            {data.zones.map((zone) => (
              <li key={zone.code} className="control__row">
                <span className="type-caption">{notTranslatable(zone.name)}</span>
                <span className="control__key type-mono-data">{notTranslatable(zone.code)}</span>
                <span className="type-mono-data">{notTranslatable(zone.kind)}</span>
              </li>
            ))}
          </ul>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.departments")}>
        {data.departments === null ? (
          <Unavailable strings={strings} />
        ) : data.departments.length === 0 ? (
          <p className="type-caption">{t(strings, "control.empty")}</p>
        ) : (
          <ul className="control__rows">
            {data.departments.map((department) => (
              <li key={department.code} className="control__row">
                <span className="type-caption">{notTranslatable(department.name)}</span>
                <span className="control__key type-mono-data">
                  {notTranslatable(department.code)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.calendars")}>
        {data.calendars === null ? (
          <Unavailable strings={strings} />
        ) : data.calendars.length === 0 ? (
          <p className="type-caption">{t(strings, "control.empty")}</p>
        ) : (
          <ul className="control__rows">
            {data.calendars.map((calendar) => (
              <li key={calendar.code} className="control__row">
                <span className="type-caption">{notTranslatable(calendar.name)}</span>
                <span className="control__key type-mono-data">
                  {notTranslatable(calendar.timezone)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.locales")}>
        {data.coverage === null ? (
          <Unavailable strings={strings} />
        ) : (
          <ul className="control__rows">
            {data.coverage.map((locale_) => (
              <li key={locale_.locale} className="control__row">
                <span className="control__key type-mono-data">
                  {notTranslatable(locale_.locale)}
                </span>
                <span className="type-caption">
                  {t(strings, "control.coverage", {
                    translated: locale_.translated,
                    translatable: locale_.translatable,
                  })}
                </span>
                {/*
                 * The missing keys are counted, not listed. A locale halfway
                 * through translation has hundreds; a list would bury the
                 * ratio, which is the number that tells an administrator
                 * whether the locale is usable. The keys themselves belong in
                 * an export, and that is a Phase 18 item rather than this
                 * panel's job.
                 */}
                <span className="type-caption">
                  {t(strings, "control.coverage.missing", {
                    count: locale_.missing_keys.length,
                  })}
                </span>
              </li>
            ))}
          </ul>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.tenants")}>
        <TenantForm strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "control.publication")}>
        <PublicationControl strings={strings} />
      </ConsolePrint>

      <p className="control__generated type-mono-data">
        {formatLedgerTime(new Date().toISOString(), locale)}
      </p>
    </div>
  );
}

function Unavailable({ strings }: { readonly strings: Strings }) {
  return <p className="control__unavailable type-caption">{t(strings, "control.unavailable")}</p>;
}

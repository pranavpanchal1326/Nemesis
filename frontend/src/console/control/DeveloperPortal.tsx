import { formatLedgerTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import type { DeveloperPortalData } from "@/server/control-data";

import { ConsolePrint } from "../ConsoleShell";
import "./control.css";

/**
 * The developer portal — §E19, §E14.4. **REAL**: eight integration paths are
 * shipped.
 *
 * Four panels, and each one exists because an integrator asks a question the
 * others cannot answer: *what may call us*, *what do we call*, *how much is
 * happening*, and *how long have I got*.
 *
 * **A key is shown by prefix and never in full.** `KeyResponse` carries
 * `key_prefix` and no secret, which is the backend refusing to make that
 * mistake possible — the full value exists once, in the response to the mint
 * call, and this screen never sees it. Rendering the prefix is what lets an
 * integrator match a row to the key in their own configuration without the
 * console becoming a place secrets can be read off a shared monitor.
 *
 * **The webhook panel leads with failures, not with URLs.** `consecutive_
 * failures` and `disabled_reason` are the two fields somebody opens this screen
 * for; the endpoint URL is what they already know. An alphabetical list of URLs
 * with the failure count in the last column is a list where the broken one is
 * invisible.
 *
 * **The version registry is a clock.** `days_until_sunset` is rendered as a
 * countdown rather than as a date, because a date requires the reader to do the
 * subtraction and the whole purpose of a deprecation notice is that they do
 * not. The date is there too, on the same row — §16.2's habit of showing the
 * derived figure beside the record it came from.
 */
export function DeveloperPortal({
  data,
  strings,
  locale,
}: {
  readonly data: DeveloperPortalData;
  readonly strings: Strings;
  readonly locale: string;
}) {
  return (
    <div className="control">
      <ConsolePrint title={t(strings, "dev.keys")}>
        {data.keys === null ? (
          <Unavailable strings={strings} />
        ) : data.keys.length === 0 ? (
          <p className="type-caption">{t(strings, "control.empty")}</p>
        ) : (
          <table className="control__table">
            <thead>
              <tr>
                <th scope="col" className="type-micro">
                  {t(strings, "dev.keys.prefix")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "dev.keys.name")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "dev.keys.quota")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "dev.keys.lastUsed")}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.keys.map((key) => (
                <tr key={key.id} data-revoked={key.revoked_at === null ? "false" : "true"}>
                  <th scope="row" className="type-mono-data">
                    {notTranslatable(key.key_prefix)}
                  </th>
                  <td className="type-caption">{notTranslatable(key.name)}</td>
                  <td className="type-mono-data">{String(key.quota_per_hour)}</td>
                  <td className="type-mono-data">
                    {key.revoked_at !== null
                      ? t(strings, "dev.keys.revoked", {
                          time: formatLedgerTime(key.revoked_at, locale),
                        })
                      : key.last_used_at === null
                        ? t(strings, "dev.keys.never")
                        : formatLedgerTime(key.last_used_at, locale)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "dev.webhooks")}>
        {data.webhooks === null ? (
          <Unavailable strings={strings} />
        ) : data.webhooks.length === 0 ? (
          <p className="type-caption">{t(strings, "control.empty")}</p>
        ) : (
          <ul className="control__rows">
            {data.webhooks.map((hook) => (
              <li key={hook.id} className="control__webhook">
                {hook.consecutive_failures === 0 ? null : (
                  <p className="control__failing type-caption">
                    {t(strings, "dev.webhooks.failures", { count: hook.consecutive_failures })}
                  </p>
                )}
                {hook.disabled_reason === null ? null : (
                  <p className="control__failing type-caption">
                    {t(strings, "dev.webhooks.disabled", { reason: hook.disabled_reason })}
                  </p>
                )}
                <p className="type-mono-data">{notTranslatable(hook.url)}</p>
                <p className="type-caption">{notTranslatable(hook.event_types.join(", "))}</p>
                <p className="type-mono-data">
                  {t(strings, "dev.webhooks.secret", { version: hook.secret_version })}{" "}
                  {notTranslatable(hook.secret_fingerprint)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "dev.usage")}>
        {data.usage === null ? (
          <Unavailable strings={strings} />
        ) : (
          <>
            <p className="type-caption">
              {t(strings, "dev.usage.window", {
                total: data.usage.total_requests,
                since: formatLedgerTime(data.usage.since, locale),
                until: formatLedgerTime(data.usage.until, locale),
              })}
            </p>
            <table className="control__table">
              <thead>
                <tr>
                  <th scope="col" className="type-micro">
                    {t(strings, "dev.usage.endpoint")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "dev.usage.requests")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "dev.usage.errors")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "dev.usage.throttled")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.usage.rows.map((row) => (
                  <tr key={`${row.usage_date}:${row.key_prefix}:${row.endpoint}`}>
                    <th scope="row" className="type-mono-data">
                      {notTranslatable(row.endpoint)}
                    </th>
                    <td className="type-mono-data">{String(row.request_count)}</td>
                    <td className="type-mono-data">{String(row.error_count)}</td>
                    <td className="type-mono-data">{String(row.throttled_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "dev.versions")}>
        {data.versions === null ? (
          <Unavailable strings={strings} />
        ) : (
          <>
            <p className="type-caption">
              {t(strings, "dev.versions.policy", {
                policy: data.versions.policy,
                days: data.versions.notice_period_days,
              })}
            </p>
            <ul className="control__rows">
              {data.versions.versions.map((version) => (
                <li key={version.name} className="control__row">
                  <span className="control__key type-mono-data">
                    {notTranslatable(version.name)}
                  </span>
                  <span className="type-caption">{notTranslatable(version.status)}</span>
                  <span className="type-caption">
                    {version.days_until_sunset === null
                      ? t(strings, "dev.versions.noSunset")
                      : t(strings, "dev.versions.countdown", {
                          days: version.days_until_sunset,
                        })}
                  </span>
                  {version.sunset_on === null ? null : (
                    <span className="type-mono-data">
                      {t(strings, "dev.versions.sunset", { date: version.sunset_on })}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </ConsolePrint>
    </div>
  );
}

function Unavailable({ strings }: { readonly strings: Strings }) {
  return <p className="control__unavailable type-caption">{t(strings, "control.unavailable")}</p>;
}

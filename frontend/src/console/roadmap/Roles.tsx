import { t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.0 — roles change the shell, not just the permissions. **ROADMAP
 * (Phase 13).**
 *
 * > Field staff never see a kanban. They see three jobs and a camera (§E21).
 *
 * That line is why this screen is a *table of the design*, rendered now, rather
 * than a settings page waiting for a session. The distinction §E19.0 is making
 * — a role changes what the product *is*, not which buttons are greyed out — is
 * an architectural commitment, and the way it usually dies is that Phase 13
 * arrives, adds a claim to a token, hides four nav items, and calls it roles.
 * Writing the intended shell per role down where the rail can reach it is the
 * cheapest available defence against that.
 *
 * **There is no contract here at all, and that is the honest thing to say.**
 * There is no session, no role claim, and no `X-Tenant-ID` replacement yet —
 * `upstream.ts` says exactly this: *"What Phase 13 changes: `resolveTenant()`.
 * Nothing else."* So nothing on this screen is a fixture *of* anything. It is
 * the specification, rendered.
 */

export function Roles({ strings }: { readonly strings: Strings; readonly locale: string }) {
  const roles = [
    "commissioner",
    "departmentHead",
    "zoneOfficer",
    "field",
    "auditor",
    "contractor",
    "support",
  ] as const;

  return (
    <div className="roadmap">
      <FixtureNotice phase="13" strings={strings} />

      <ConsolePrint title={t(strings, "roles.role")}>
        <p className="roadmap__why type-caption">{t(strings, "roles.why")}</p>
        <table className="roadmap__table">
          <thead>
            <tr>
              <th scope="col" className="type-micro">
                {t(strings, "roles.role")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "roles.sees")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "roles.can")}
              </th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role}>
                <th scope="row" className="type-caption">
                  {t(strings, `roles.${role}`)}
                </th>
                <td className="type-caption">{t(strings, `roles.${role}.sees`)}</td>
                <td className="type-caption">{t(strings, `roles.${role}.can`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ContractGap what={t(strings, "gap.roles")} strings={strings} />
      </ConsolePrint>
    </div>
  );
}

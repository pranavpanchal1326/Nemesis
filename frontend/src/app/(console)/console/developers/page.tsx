import { ConsoleShell } from "@/console/ConsoleShell";
import { DeveloperPortal } from "@/console/control/DeveloperPortal";
import { screenById } from "@/console/screens";
import { consoleContext } from "@/server/console-context";
import { fetchDeveloperPortal } from "@/server/control-data";

/**
 * §E19, §E14.4 — the developer portal. **REAL**: eight integration paths are
 * shipped.
 *
 * Under `/console` rather than beside `/developers`, and the two are different
 * things despite the name. `/developers` is the `(dev)` route group: proof
 * surfaces that exist so a gate can be asserted against a real render, and that
 * 404 in production. This is a tenant-facing administrative screen about *their*
 * keys, *their* webhooks and *their* usage, and it belongs in the console with
 * the rest of the control plane.
 */
export default async function Page() {
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("developers");
  if (screen === undefined) throw new Error("unknown console screen: developers");

  const data = await fetchDeveloperPortal();

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <DeveloperPortal data={data} strings={strings} locale={locale} />
    </ConsoleShell>
  );
}

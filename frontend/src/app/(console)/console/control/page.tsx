import { ConsoleShell } from "@/console/ConsoleShell";
import { ControlPlane } from "@/console/control/ControlPlane";
import { screenById } from "@/console/screens";
import { consoleContext } from "@/server/console-context";
import { fetchControlPlane } from "@/server/control-data";

/**
 * §E19, §E14.4, ADR-0046 — the control plane. **REAL**: 35 paths are shipped.
 *
 * F6 gate: *a tenant is provisioned, given an invented taxonomy, and published
 * — entirely through the UI, no SQL, no code change.* All three writes are on
 * this screen, and the reads beside them are what make the gate checkable
 * rather than watchable.
 */
export default async function Page() {
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("control");
  if (screen === undefined) throw new Error("unknown console screen: control");

  const data = await fetchControlPlane();

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <ControlPlane data={data} strings={strings} locale={locale} />
    </ConsoleShell>
  );
}

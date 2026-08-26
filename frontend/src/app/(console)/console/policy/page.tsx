import { PolicyStudio } from "@/console/policy/PolicyStudio";
import { ConsoleShell } from "@/console/ConsoleShell";
import { screenById } from "@/console/screens";
import { POLICY_KINDS, type PolicyKind } from "@/generated/enums";
import { consoleContext } from "@/server/console-context";
import { fetchPolicyStudio } from "@/server/policy-data";

/**
 * §E19.8 — the policy studio. **REAL**; the backend is fully shipped.
 *
 * The kind and revision are search parameters rather than path segments, and
 * that is a deliberate small thing: an operator comparing revision 7 to
 * revision 8 edits one number in the address bar, and a link to *"the document
 * that was active when this went wrong"* is a URL somebody can paste into an
 * incident review.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string; revision?: string }>;
}) {
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("policy");
  if (screen === undefined) throw new Error("unknown console screen: policy");

  const params = await searchParams;
  const kind = readKind(params.kind);
  const revision = readRevision(params.revision);
  const data = await fetchPolicyStudio(kind, revision);

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <PolicyStudio data={data} strings={strings} locale={locale} />
    </ConsoleShell>
  );
}

/** An unrecognised kind falls back to the first published one rather than
 *  404ing: a mistyped query string should land an operator on a working screen,
 *  and the rail above it says which kind they are looking at. */
function readKind(value: string | undefined): PolicyKind {
  const fallback = POLICY_KINDS[0];
  if (value === undefined) return fallback;
  return (POLICY_KINDS as readonly string[]).includes(value) ? (value as PolicyKind) : fallback;
}

function readRevision(value: string | undefined): number | undefined {
  if (value === undefined) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

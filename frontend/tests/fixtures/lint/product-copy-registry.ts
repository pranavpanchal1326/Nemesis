// A17 / ADR-0058: the third string tier, re-added the way it would actually
// come back — as three plausible lines in a bundle loader.
export async function loadOne(namespace: string, locale: string) {
  return fetch(`/api/v1/control-plane/translations/{namespace}/{locale}`, {
    headers: { namespace, locale },
  });
}

// The BFF proxy that used to front it. Deleted at F18; banned so it stays that
// way.
export const proxied = "/api/i18n/public/mr";

// Legitimate, and deliberately in this fixture so the ban is asserted to be
// narrow: coverage is the *tenant-authored* half, read by the control-plane
// admin screen, and is not what ADR-0058 removed.
export const coverage = "/api/v1/control-plane/translations/coverage";

import type { NextConfig } from "next";

/**
 * §E14.4 — one application, five route groups.
 *
 * `typedRoutes` is on because §E24's "not wired" chip has to be enforceable at
 * the routing layer: a ROADMAP screen that cannot be linked to is a screen that
 * cannot leak to a public URL by accident.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  poweredByHeader: false,
};

// Note: Next 16 removed build-time linting entirely, so there is no
// `eslint.ignoreDuringBuilds` to set. `npm run lint` is the only lint pass,
// which is what M0's gate wanted anyway — one command, no second opinion.

export default nextConfig;

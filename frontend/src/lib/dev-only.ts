import { notFound } from "next/navigation";

/**
 * §E24 — "the 'not wired' chip … **cannot be routed to a public URL**".
 *
 * The blueprint states that rule for screens whose backing phase has not
 * landed, and it states it as a build-enforced property rather than a
 * discipline: *"Track E races ahead of the backend without ever lying about
 * it — §6 Principle #8 enforced by the build, not by discipline."*
 *
 * The same mechanism covers proof pages — surfaces that exist so a gate can be
 * asserted against a real render rather than a mock. They are real routes in
 * development, and they are 404 in a production build. One helper, so there is
 * one place to read and one place to get it wrong.
 *
 * Call it at the top of a server component, before anything else runs.
 */
export function devOnly(): void {
  if (process.env.NODE_ENV === "production") notFound();
}

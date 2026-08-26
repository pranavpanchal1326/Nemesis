import Link from "next/link";

/**
 * The index for the §E24 proof surfaces.
 *
 * **It was `<h1>Developers</h1>` and nothing else** — a page with a heading, no
 * links, and six unlisted routes beneath it that only somebody reading
 * `tests/*.spec.ts` would ever find. The proofs are the surfaces this
 * repository points at when it claims something is real; a reviewer who cannot
 * reach them without a grep is a reviewer who takes the claim on trust, which
 * is the one thing §E3.3 is written to prevent.
 *
 * **Not the developer *portal*.** That is `/console/developers` — keys,
 * webhooks, usage, the deprecation clock — and it is a REAL screen behind the
 * console shell. This is the group of proof routes, and the distinction is
 * named here because the two names are one word apart.
 *
 * Plain English rather than `t()`, matching the proof pages themselves: these
 * are internal surfaces read by whoever is reviewing the build, and the §E10.1
 * argument for routing every citizen-facing string through the bundles is an
 * argument about a citizen-facing string.
 */

const PROOFS: readonly { readonly href: string; readonly title: string; readonly what: string }[] =
  [
    {
      href: "/developers/proof/clay",
      title: "The clay engine",
      what: "§E23's budgets from renderer.info, every fallback tier, and the peer list beside the canvas.",
    },
    {
      href: "/developers/proof/story",
      title: "The Walk",
      what: "§E16's nine acts at a fixed seed and step, plus the Tier C storyboard.",
    },
    {
      href: "/developers/proof/press",
      title: "The press",
      what: "§E6's print pipeline, and the rule that text layers are byte-identical with it on and off.",
    },
    {
      href: "/developers/proof/contracts",
      title: "The §E26 contract matrix",
      what: "All ten component contracts in every state, at three densities and in both scripts.",
    },
    {
      href: "/developers/proof/public",
      title: "The §E18 states",
      what: "Suppression, the flagged frame, and the rule that a withheld figure never renders as a zero.",
    },
    {
      href: "/developers/proof/type",
      title: "The type scale",
      what: "§E10 in both scripts, with the Devanagari line-height the generator applies.",
    },
  ];

export default function Developers() {
  return (
    <div className="dev-index">
      <h1 className="type-title">Proof surfaces</h1>
      <p className="type-body">
        Each of these renders a claim this build makes, so it can be looked at rather than taken on
        trust. All six are dev-only: <code className="type-mono-data">devOnly()</code> makes them a
        404 in a production build (§E24).
      </p>

      <ul className="dev-index__list">
        {PROOFS.map((proof) => (
          <li key={proof.href}>
            <Link className="dev-index__link" href={proof.href}>
              <span className="type-heading">{proof.title}</span>
              <span className="type-caption">{proof.what}</span>
            </Link>
          </li>
        ))}
      </ul>

      <p className="type-caption">
        Looking for the developer portal — keys, webhooks, usage, the version clock? That is{" "}
        <Link href="/console/developers">inside the console</Link>, and it is a different thing with
        an almost identical name.
      </p>
    </div>
  );
}

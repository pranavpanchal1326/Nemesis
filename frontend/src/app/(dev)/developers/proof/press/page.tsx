import { Press } from "@/press/Press";
import { devOnly } from "@/lib/dev-only";
import type { InkSetName, PressQuality, SeverityLevel } from "@/design/generated/tokens";

/**
 * The press, proved rather than described.
 *
 * §E25's Phase 18 gate has two clauses this page exists to make assertable:
 *
 *   · the press renders identically in 2D and 3D at a fixed seed
 *   · **text layers are byte-identical with the press on and off**
 *
 * `tests/press-text-exempt.spec.ts` drives this route with `?bypass=1` and
 * compares the text layer's pixels between the two renders. Nothing here is a
 * mock: it is `<Press>` as shipped, at a fixed seed, over a subject chosen so
 * a separation failure is visible to a person as well as to a byte comparison.
 *
 * Dev-only, per §E24 — a proof surface is not a public URL.
 */
export default async function PressProof({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  devOnly();
  const params = await searchParams;
  const one = (key: string): string | undefined => {
    const value = params[key];
    return Array.isArray(value) ? value[0] : value;
  };

  const bypass = one("bypass") === "1";
  const surface = (one("surface") ?? "citizen") as InkSetName;
  const quality = (one("quality") ?? "full") as PressQuality;
  const severityParam = one("severity");
  const severity = severityParam === undefined ? undefined : (severityParam as SeverityLevel);
  const seed = Number(one("seed") ?? "1");

  return (
    <div style={{ padding: "2rem", background: "var(--color-paper-50)" }}>
      <Press
        surface={surface}
        quality={quality}
        seed={seed}
        bypass={bypass}
        {...(severity === undefined ? {} : { severity })}
        className="press-proof"
        imagery={<ProofSubject />}
      >
        {/*
         * The text layer. Opaque on purpose: an element screenshot of an
         * opaque layer captures that layer and nothing beneath it, which is
         * what makes "byte-identical" a claim about the text rather than a
         * claim about whatever happened to be behind it.
         */}
        <div
          data-proof="text"
          style={{
            background: "var(--color-chalk)",
            padding: "1rem 1.25rem",
            // Integer dimensions on purpose. An element whose height lands on a
            // fraction of a pixel has one half-covered boundary row, and that
            // row blends with whatever is behind it — which is exactly the
            // thing the press changes. Without this the gate compares the
            // fixture's edge rather than the type, and fails for a reason that
            // has nothing to do with §E6.2.
            boxSizing: "border-box",
            width: "352px",
            height: "224px",
            overflow: "hidden",
          }}
        >
          <p className="type-micro" style={{ margin: 0, color: "var(--role-text-secondary)" }}>
            Ward 14 · Kothrud
          </p>
          <h1 className="type-heading" style={{ margin: "0.25rem 0 0.5rem" }}>
            Text prints solid.
          </h1>
          <p className="type-body" style={{ margin: 0 }}>
            No halftone, no offset, 100% density — which is also physically true of a risograph.
            Type is solid ink; images are screened.
          </p>
          <p className="type-mono-data" style={{ margin: "0.75rem 0 0" }}>
            9f2c41ab · 18.5074, 73.8077 · 2026-08-24T11:04:19Z
          </p>
        </div>
      </Press>
    </div>
  );
}

/**
 * Deliberately not a photograph. Flat fields at known values make a separation
 * failure legible: if an ink drops or an angle collides, this is where it shows.
 */
function ProofSubject() {
  const bands = ["--color-riso-brown", "--color-riso-sunflower", "--color-riso-aqua"];
  return (
    <div data-proof="imagery" style={{ width: "22rem", height: "14rem", display: "grid" }}>
      <div style={{ display: "flex" }}>
        {bands.map((band) => (
          <div key={band} style={{ flex: 1, background: `var(${band})` }} />
        ))}
      </div>
      <div
        style={{
          background: "linear-gradient(to right, var(--color-riso-black), var(--color-paper-50))",
        }}
      />
    </div>
  );
}

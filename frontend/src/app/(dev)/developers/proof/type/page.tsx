import { devOnly } from "@/lib/dev-only";
import { TYPE_STEPS, MEASURE_CH } from "@/design/generated/tokens";

/**
 * The type stack, proved rather than described — §E10, §E10.1, §E22.
 *
 * Three claims are asserted against this page by `tests/type.spec.ts`:
 *
 *   · **no request for a face leaves the origin** (§6 Principle #6). Every
 *     woff2 is committed under `public/fonts/`, so the air-gapped bootstrap
 *     Phase 29 gates on cannot be broken by a missing network.
 *   · **Devanagari carries +0.15 line-height over the Latin value** (§E10.1),
 *     applied by the token generator rather than by a stylesheet override —
 *     so it is measurable, and it cannot be quietly disagreed with.
 *   · **every step renders in both scripts without clipping the shirorekha.**
 *
 * §E10.1's whole argument is that Devanagari is a design partner and not a
 * fallback. A page that only shows Latin is how that argument gets lost.
 */
export default function TypeProof() {
  devOnly();

  return (
    <div style={{ padding: "2rem", maxWidth: `${String(MEASURE_CH)}ch` }}>
      <h1 className="type-title" style={{ marginTop: 0 }}>
        The type scale, in both scripts
      </h1>

      <section lang="en" data-script="latin">
        {TYPE_STEPS.map((step) => (
          <p key={step} className={`type-${step}`} data-step={step} data-lang="en">
            {LATIN[step]}
          </p>
        ))}
      </section>

      {/*
       * `lang` is not decoration here. The per-script scale keys off `:lang()`,
       * which is also what a screen reader keys off to pick a voice — the same
       * attribute doing both jobs is why §E10.1's rule is enforceable at all.
       */}
      <section lang="mr" data-script="devanagari">
        {TYPE_STEPS.map((step) => (
          <p key={step} className={`type-${step}`} data-step={step} data-lang="mr">
            {DEVANAGARI[step]}
          </p>
        ))}
      </section>
    </div>
  );
}

/** Real strings, not lorem. A specimen set in nonsense proves nothing about a
 *  product whose type has to carry ward names, rupees and chain hashes. */
const LATIN: Record<string, string> = {
  poster: "Ward 14",
  "display-1": "Prove, don't log.",
  "display-2": "A pothole gets reported.",
  title: "Kothrud — open complaints",
  heading: "Severity raised to High",
  body: "You're the 4th person to report this. First reported 6 days ago.",
  caption: "Withheld to protect reporters — fewer than 5 reports.",
  micro: "Unverified · response requested",
  "mono-data": "9f2c41ab · 18.5074, 73.8077 · ₹ 1,24,500",
  doc: "This record cannot be edited. Corrections are added, never overwritten.",
  hand: "reported 14 Mar — no closure",
};

const DEVANAGARI: Record<string, string> = {
  poster: "प्रभाग १४",
  "display-1": "सिद्ध करा, नोंदवू नका.",
  "display-2": "खड्ड्याची तक्रार नोंदवली जाते.",
  title: "कोथरूड — प्रलंबित तक्रारी",
  heading: "तीव्रता वाढवून 'उच्च' केली",
  body: "ही तक्रार नोंदवणारे तुम्ही चौथे नागरिक आहात. पहिली नोंद ६ दिवसांपूर्वी.",
  caption: "तक्रारदारांच्या संरक्षणासाठी माहिती रोखली आहे.",
  micro: "अपुष्ट · उत्तर मागवले",
  "mono-data": "9f2c41ab · 18.5074, 73.8077 · ₹ १,२४,५००",
  doc: "ही नोंद बदलता येत नाही. दुरुस्त्या जोडल्या जातात, पुसल्या जात नाहीत.",
  hand: "१४ मार्च रोजी नोंद — निपटारा नाही",
};

import { expect, test } from "./fixtures/origin";

/**
 * A1's gate — metric-matched fallbacks, §E10, §E15, ADR-0053.
 *
 * The register's complaint was precise: *"the real woff2 files ship and the
 * fallback stacks are correct, but no `size-adjust` / `ascent-override` /
 * `descent-override` / `line-gap-override` is declared on a fallback
 * `@font-face`."* `scripts/fetch_fonts.py` now generates one adjusted face per
 * role from the real face's own tables, and `--verify` recomputes the numbers
 * and fails if the file disagrees with them.
 *
 * That check proves the CSS says what the metrics say. It cannot prove the
 * browser agrees, and the browser is where the claim is either true or a
 * comment. So there are two assertions here, and they answer different
 * questions:
 *
 * 1. **Does an adjusted face occupy the real face's line box?** Measured
 *    directly, at `line-height: normal`, against the real face on the same
 *    page. This is the claim itself, and it is deterministic — no timing, no
 *    network, no threshold to argue about.
 * 2. **Does the swap move the page?** The gate F2 actually names: CLS < 0.1 on
 *    the citizen route with the real faces delayed, so the first paint is the
 *    fallback and the swap happens under observation. §E23 budgets it; A1's
 *    consequence line says it is *"the metric nobody notices until Lighthouse
 *    says so."*
 */

/** The ten roles, the adjusted face each one now carries, and the string each
 *  is measured with.
 *
 *  **The probe text is per script, and that is not cosmetic.** A line
 *  containing a glyph the named family does not have is composed from two
 *  fonts, and the line box is the union of both — so measuring a Devanagari
 *  display face with `"Hxpq खड्डा"` measures Sarpanch's Devanagari against
 *  whatever the engine chose for the Latin, and reports a 3 px mismatch that
 *  belongs to neither. Found exactly that way.
 *
 *  Restated here rather than imported from the token module on purpose: this
 *  file is the independent reader. A test that derives its expectations from
 *  the same source as the code cannot notice when that source is wrong. */
const LATIN = "Hxpq";
const DEVANAGARI = "खड्डा";

const ROLES: readonly {
  readonly real: string;
  readonly fallback: string;
  readonly probe: string;
}[] = [
  { real: "Gambarino", fallback: "Gambarino Fallback", probe: LATIN },
  { real: "Panchang", fallback: "Panchang Fallback", probe: LATIN },
  { real: "Switzer", fallback: "Switzer Fallback", probe: LATIN },
  { real: "JetBrains Mono", fallback: "JetBrains Mono Fallback", probe: LATIN },
  { real: "Courier Prime", fallback: "Courier Prime Fallback", probe: LATIN },
  // One hand across both scripts (§E10), measured in Latin because that is
  // where it carries running text.
  { real: "Kalam", fallback: "Kalam Fallback", probe: LATIN },
  { real: "Sarpanch", fallback: "Sarpanch Fallback", probe: DEVANAGARI },
  {
    real: "Tiro Devanagari Marathi",
    fallback: "Tiro Devanagari Marathi Fallback",
    probe: DEVANAGARI,
  },
  { real: "Noto Sans Devanagari", fallback: "Noto Sans Devanagari Fallback", probe: DEVANAGARI },
  { real: "Modak", fallback: "Modak Fallback", probe: DEVANAGARI },
];

/** The citizen route. §E23 budgets LCP and CLS here before anywhere else. */
const CITIZEN = "/report";

/** Long enough that the first paint is unambiguously the fallback. */
const FONT_DELAY_MS = 900;

test.describe("metric-matched fallbacks (A1)", () => {
  test("every adjusted face occupies its real face's line box", async ({ page }) => {
    await page.goto(CITIZEN);
    await page.evaluate(async () => {
      await document.fonts.ready;
    });

    const measured = await page.evaluate(async (roles) => {
      /** Height of one line at `line-height: normal` — which is exactly
       *  (ascent + descent + line gap), the three descriptors under test. */
      async function lineBox(family: string, text: string): Promise<number | null> {
        // `load` resolves with the faces that matched. For a `local()`-only
        // face that is empty on a machine without any of the named system
        // faces, and a role measured there would be measuring the generic
        // fallback instead — a passing assertion about nothing.
        const matched = await document.fonts.load(`100px "${family}"`, text);
        if (family.endsWith("Fallback") && matched.length === 0) return null;

        const probe = document.createElement("div");
        probe.textContent = text;
        probe.style.cssText = [
          "position:absolute",
          "visibility:hidden",
          "white-space:nowrap",
          "line-height:normal",
          "font-size:100px",
          `font-family:"${family}"`,
        ].join(";");
        document.body.append(probe);
        const height = probe.getBoundingClientRect().height;
        probe.remove();
        return height;
      }

      const out: { real: string; realBox: number | null; fallbackBox: number | null }[] = [];
      for (const role of roles) {
        out.push({
          real: role.real,
          realBox: await lineBox(role.real, role.probe),
          fallbackBox: await lineBox(role.fallback, role.probe),
        });
      }
      return out;
    }, ROLES);

    const resolved = measured.filter((row) => row.fallbackBox !== null);

    // If nothing resolved, the machine has none of the named system faces and
    // this assertion has measured nothing. Fail rather than pass quietly: a
    // green tick that means "not checked" is the failure mode F1 spent a whole
    // phase eliminating.
    expect(
      resolved.length,
      "no adjusted fallback resolved to an installed face — this run asserted nothing",
    ).toBeGreaterThan(0);

    for (const row of resolved) {
      // One pixel at a 100 px em: the descriptors are stated to two decimal
      // places, so the residue is rounding and nothing else.
      expect(row.fallbackBox, `${row.real}: fallback line box`).toBeCloseTo(row.realBox ?? 0, 0);
    }
  });

  test("the swap does not move the citizen route — CLS < 0.1", async ({ page }) => {
    // Delay rather than block. A blocked face never arrives, so it never swaps,
    // and a CLS of zero would be measuring the absence of the event under test.
    await page.route("**/fonts/**", async (route) => {
      const response = await route.fetch();
      await new Promise((resolve) => setTimeout(resolve, FONT_DELAY_MS));
      await route.fulfill({ response });
    });

    await page.addInitScript(() => {
      const store = window as unknown as { __cls: number };
      store.__cls = 0;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as (PerformanceEntry & {
          value: number;
          hadRecentInput: boolean;
        })[]) {
          // The same exclusion Lighthouse and the Web Vitals library apply:
          // a shift the user caused by tapping something is not a defect.
          if (!entry.hadRecentInput) store.__cls += entry.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });

    await page.goto(CITIZEN);
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    // The shift is recorded on the frame after the swap, not on the promise.
    await page.waitForTimeout(500);

    const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    expect(cls, "cumulative layout shift with the real faces delayed").toBeLessThan(0.1);
  });
});

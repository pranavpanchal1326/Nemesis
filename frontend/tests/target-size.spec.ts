import { expect, test, type Page } from "@playwright/test";

/**
 * WCAG 2.2 AA — 2.5.8 Target Size (Minimum), 24 × 24 CSS px.
 *
 * **The criterion `axe` does not test, on the surfaces most likely to be
 * opened on a phone.** 2.5.8 is new in WCAG 2.2 and needs layout geometry
 * rather than a DOM rule, so a clean `axe` pass says nothing about it — which
 * is the gap [`docs/reports/wcag-audit-gap.md`](../../docs/reports/wcag-audit-gap.md)
 * describes in the abstract, and this file is one concrete piece of it made
 * automatable.
 *
 * It was failing. The language switch on the public pages rendered three links
 * at 15 × 18, 16 × 18 and 12 × 18 px, side by side, on the §E18 surface a
 * journalist or a resident reaches from a search result on their phone. The
 * citizen surface's way back to its door was 94 × 14.
 *
 * **The exceptions are the standard's own, and they are named rather than
 * assumed:**
 *
 *   · *Inline* — a link inside a sentence, whose size is constrained by the
 *     line height of the text around it. §E18's prose carries several, and
 *     enlarging them would break the paragraph they live in. Detected
 *     structurally: the anchor shares a text-bearing parent with other content.
 *   · *Equivalent* — a control with another way to reach the same function on
 *     the same page.
 *   · Anything not actually rendered: a visually hidden file input behind a
 *     labelled button is the pattern §E17's shutter uses, and the button is the
 *     target.
 *
 * The console is deliberately absent from the list. §E19 is a pointer-and-
 * keyboard surface on a light table, 2.5.8's exception for *user-agent
 * controls* does not cover it, and claiming a pass there by not looking would
 * be worse than the gap. It is named in the report instead.
 */

const MINIMUM = 24;

/** The surfaces a person reaches on a phone. */
const ROUTES: readonly (readonly [name: string, path: string])[] = [
  ["the resident's door", "/citizen"],
  ["the staff door", "/staff"],
  ["the report flow", "/report"],
  ["the field app", "/field"],
  ["the city index", "/pune-demo"],
  ["a ward page", "/pune-demo/ward/W-AUNDH"],
  ["the honesty table", "/pune-demo/honesty"],
];

interface Undersized {
  readonly tag: string;
  readonly cls: string;
  readonly text: string;
  readonly width: number;
  readonly height: number;
}

async function undersized(page: Page): Promise<Undersized[]> {
  return page.evaluate((minimum) => {
    const targets = [...document.querySelectorAll("a[href],button,input,select,[role='button']")];

    return targets
      .filter((el) => {
        const style = getComputedStyle(el);
        if (style.visibility === "hidden" || style.display === "none") return false;
        const box = el.getBoundingClientRect();
        // Off-screen by design — the skip links park at -100vw until focused,
        // and a focused skip link is a different measurement entirely.
        if (box.right <= 0 || box.bottom <= 0) return false;
        if (box.width === 0 || box.height === 0) return false;
        // A 1 × 1 input is the visually-hidden file picker behind a labelled
        // button (§E17.1's shutter). The button is the target, and it is in
        // this list on its own account.
        if (box.width <= 2 && box.height <= 2) return false;
        return box.width < minimum || box.height < minimum;
      })
      .filter((el) => {
        // The standard's *inline* exception: a link in a sentence, sized by the
        // line height of the text around it. Structural rather than a guess —
        // the anchor has a text-bearing sibling inside a paragraph-like parent.
        const parent = el.parentElement;
        if (parent === null) return true;
        const inProse = /^(P|LI|TD|DD|DT|SPAN|H1|H2|H3|H4|BLOCKQUOTE)$/.test(parent.tagName);
        const siblingText = [...parent.childNodes]
          .filter((node) => node !== el)
          .map((node) => node.textContent ?? "")
          .join("")
          .trim();
        return !(inProse && siblingText.length > 0);
      })
      .map((el) => {
        const box = el.getBoundingClientRect();
        return {
          tag: el.tagName.toLowerCase(),
          cls: el.className.split(" ").at(0) ?? "",
          text: el.textContent.trim().slice(0, 40),
          width: Math.round(box.width),
          height: Math.round(box.height),
        };
      });
  }, MINIMUM);
}

test.describe("2.5.8 — every target is at least 24 × 24 on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  for (const [name, path] of ROUTES) {
    test(`${name} — ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await expect(page.locator("body")).toBeVisible();

      const found = await undersized(page);
      expect(
        found
          .map((t) => `${t.tag}.${t.cls} ${String(t.width)}×${String(t.height)} "${t.text}"`)
          .join("\n"),
        `${path} has ${String(found.length)} target(s) under ${String(MINIMUM)}px. ` +
          "Grow the box, not the type: §E10's scale is a design decision and this is a hit area.",
      ).toBe("");
    });
  }
});

test.describe("the assertion is not vacuous", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("a deliberately tiny control is caught", async ({ page }) => {
    await page.goto("/citizen");
    await page.waitForLoadState("networkidle");
    expect(await undersized(page), "the clean route was already failing").toEqual([]);

    await page.evaluate(() => {
      const tiny = document.createElement("button");
      tiny.textContent = "x";
      tiny.style.cssText = "width:12px;height:12px;padding:0;border:0;display:block";
      document.body.appendChild(tiny);
    });

    const found = await undersized(page);
    expect(found, "an injected 12px button was not caught").toHaveLength(1);
    expect(found[0]?.width).toBe(12);
  });
});

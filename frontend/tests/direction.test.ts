import { describe, expect, it } from "vitest";

import { directionOf } from "@/lib/i18n/direction";

/**
 * A11's unit half — §E22.
 *
 * The browser half lives in `tests/rtl.spec.ts`, which asks a real engine which
 * way a real page went. This asks the smaller question the browser cannot: that
 * the *rule* is right, including for the tags a page will actually be asked
 * for, which are regional (`ar-EG`) far more often than bare.
 */
describe("directionOf", () => {
  it("runs the product's own locales left to right", () => {
    for (const locale of ["en", "mr", "hi", "en-IN", "mr-IN"]) {
      expect(directionOf(locale), locale).toBe("ltr");
    }
  });

  it("runs the right-to-left languages right to left", () => {
    // `ur` is the one an Indian deployment is most likely to need; `ar` is what
    // the demo tenant seeds, because the assertion is about direction rather
    // than about words.
    for (const locale of ["ar", "ur", "he", "fa", "ps", "sd", "ckb", "dv", "yi"]) {
      expect(directionOf(locale), locale).toBe("rtl");
    }
  });

  it("matches on the language subtag, the way locale negotiation does", () => {
    // `negotiateLocale` resolves `mr-IN` to `mr`; a direction keyed on the full
    // tag would send an `ar-EG` reader a left-to-right frame, which is the same
    // bug one layer down.
    expect(directionOf("ar-EG")).toBe("rtl");
    expect(directionOf("AR-eg")).toBe("rtl");
    expect(directionOf("ur-PK")).toBe("rtl");
  });

  it("does not guess at a tag it does not know", () => {
    // An unknown locale renders in the source language's words; rendering them
    // right to left as well would compound one gap into two.
    expect(directionOf("")).toBe("ltr");
    expect(directionOf("zz")).toBe("ltr");
    // `arc` is Aramaic and `ar` is Arabic; `arn` is Mapudungun and is neither.
    expect(directionOf("arn")).toBe("ltr");
  });
});

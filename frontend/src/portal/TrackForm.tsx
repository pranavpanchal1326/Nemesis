import { redirect } from "next/navigation";

import { t, type Strings } from "@/lib/i18n/strings";

/**
 * The receipt field — §E17.4, ADR-0043, §E13 Tier D.
 *
 * **Why the citizen door has a field where every other entry is a link.** A
 * complaint's id is a *capability*, not a name: ADR-0043 calibrates the
 * tracking page to "whoever holds its id", and `/t/[id]` is `noindex` for the
 * same reason. A door that listed reports would be a door that knows which
 * reports exist, which is precisely the thing this design refuses to know. So
 * the resident brings the id, and the page takes it.
 *
 * **It submits with JavaScript switched off.** A client component with a
 * `router.push` would be the shorter version and would leave Tier D — a
 * crawler, a 2G phone, a browser that failed to hydrate — looking at an input
 * that does nothing when you press Enter, which is exactly the affordance-that
 * -is-not-one §E3.3 rules out. A server action posts the form natively, so the
 * field works before hydration and without it.
 *
 * **The id is validated here rather than trusted to the route.** `/t/{id}`
 * takes any string and asks the API about it; a mistyped receipt would become
 * an upstream 404 rendered as an error screen. A UUID is a shape this side can
 * check, so a typo is answered by the field that produced it, in the resident's
 * own language, with what they typed still in the box.
 */

/** UUIDv4, which is what `POST /complaints` puts on a receipt. Any version and
 *  any casing is accepted: the shape is what this side can honestly check, and
 *  refusing a valid-but-v7 id would be this form inventing a rule. */
const RECEIPT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function TrackForm({
  strings,
  /** What the last submission was rejected for, round-tripped as a search
   *  param so the message survives a POST with no JavaScript to hold it. */
  rejected,
}: {
  readonly strings: Strings;
  readonly rejected: string | undefined;
}) {
  // A Server Action must be `async` — Next inspects the signature, and a
  // synchronous one is a build error rather than a style preference. Nothing in
  // this body awaits, because validating a UUID and issuing a redirect is all
  // it does; `redirect()` throws its own control-flow signal rather than
  // returning, which is why there is no `return` after it either.
  // eslint-disable-next-line @typescript-eslint/require-await
  async function track(formData: FormData): Promise<void> {
    "use server";

    const raw = formData.get("receipt");
    const id = typeof raw === "string" ? raw.trim() : "";

    if (!RECEIPT_ID.test(id)) {
      // The rejected value goes back in the box. Clearing a field somebody
      // typed twenty-eight characters into is how a form teaches people to
      // give up on it.
      redirect(`/citizen?rejected=${encodeURIComponent(id.slice(0, 64))}`);
    }

    redirect(`/t/${id}`);
  }

  return (
    <section className="portal__track" aria-labelledby="track-title">
      <h2 className="portal__group-title type-micro" id="track-title">
        {t(strings, "portal.track")}
      </h2>
      <p className="portal__card-hint type-caption">{t(strings, "portal.track.hint")}</p>

      <form action={track} className="portal__track-row">
        <label className="type-caption" htmlFor="receipt">
          {t(strings, "portal.track.label")}
        </label>
        <input
          className="portal__track-input"
          id="receipt"
          name="receipt"
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          defaultValue={rejected ?? ""}
          aria-describedby={rejected === undefined ? undefined : "receipt-error"}
          aria-invalid={rejected === undefined ? undefined : true}
          placeholder={t(strings, "portal.track.placeholder")}
        />
        <button className="portal__track-submit type-caption" type="submit">
          {t(strings, "portal.track.submit")}
        </button>
      </form>

      {rejected === undefined ? null : (
        <p className="portal__track-error type-caption" id="receipt-error" role="alert">
          {t(strings, "portal.track.rejected")}
        </p>
      )}
    </section>
  );
}

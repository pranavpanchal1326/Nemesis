"use client";

import { useId, useState } from "react";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { useWrite } from "./forms";

/**
 * Publication — ADR-0046, F6, §16.2.
 *
 * > publication is an act somebody takes, logged as an `admin_action` with a
 * > required justification, revocable through the same door.
 *
 * F6 asks for *"the publication control from ADR-0046, with its justification
 * field as a first-class input rather than a hidden parameter"*, and the whole
 * design of this component follows from that phrase.
 *
 * **The justification is a required field beside the button, not a confirm
 * dialog.** A dialog that appears after the decision collects a sentence
 * somebody types to get past it. A field that must be filled *before* the
 * button enables collects a sentence somebody wrote while deciding — and the
 * `admin_action` row is only worth having if it holds the second kind.
 *
 * **Turning publication off uses the same door.** One control, two directions,
 * one justification requirement. A product where publishing needs a reason and
 * un-publishing does not is a product where the reversible half of the decision
 * is the unaccountable one — and un-publishing is the direction that removes a
 * city's transparency page from the internet.
 *
 * **This panel sets and cannot show.** There is no `GET` for a tenant's
 * publication state on the contract; the sentence saying so is rendered rather
 * than the panel guessing. Noted here because it is a real gap and this is
 * where the next person will look for it.
 */
export function PublicationControl({ strings }: { readonly strings: Strings }) {
  const id = useId();
  const { state, send } = useWrite();
  const [slug, setSlug] = useState("");
  const [justification, setJustification] = useState("");
  const [enabled, setEnabled] = useState<boolean | null>(null);

  const ready = slug.trim() !== "" && justification.trim() !== "";

  function submit(next: boolean) {
    setEnabled(next);
    void send(
      `/api/control/tenants/${encodeURIComponent(slug.trim())}/publication`,
      { enabled: next, justification: justification.trim() },
      "PUT",
    );
  }

  return (
    <div className="control__form">
      <p className="control__why type-caption">{t(strings, "control.publication.why")}</p>
      <p className="control__why type-caption">{t(strings, "control.publication.noRead")}</p>

      <label className="type-caption" htmlFor={`${id}-slug`}>
        {t(strings, "control.tenant.slug")}
      </label>
      <input
        id={`${id}-slug`}
        className="control__field type-mono-data"
        value={slug}
        onChange={(event) => {
          setSlug(event.target.value);
        }}
      />

      <label className="type-caption" htmlFor={`${id}-justification`}>
        {t(strings, "control.publication.justification")}
      </label>
      <textarea
        id={`${id}-justification`}
        className="control__field type-body"
        rows={3}
        value={justification}
        onChange={(event) => {
          setJustification(event.target.value);
        }}
      />

      <div className="control__actions">
        <button
          type="button"
          className="control__action type-caption"
          disabled={!ready || state.kind === "sending"}
          onClick={() => {
            submit(true);
          }}
        >
          {t(strings, "control.publication.enable")}
        </button>
        <button
          type="button"
          className="control__action type-caption"
          disabled={!ready || state.kind === "sending"}
          onClick={() => {
            submit(false);
          }}
        >
          {t(strings, "control.publication.disable")}
        </button>
      </div>

      <p className="type-caption" role="status">
        {state.kind === "done"
          ? t(strings, "control.publication.done", {
              slug: slug.trim(),
              state: t(
                strings,
                enabled === true ? "control.publication.on" : "control.publication.off",
              ),
            })
          : state.kind === "refused"
            ? notTranslatable(state.detail ?? "") || t(strings, "control.publication.failed")
            : null}
      </p>
    </div>
  );
}

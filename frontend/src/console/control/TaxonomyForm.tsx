"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { useWrite } from "./forms";

/**
 * Define a defect category — Phase 5's gate, half of it.
 *
 * > a tenant is provisioned, **given an invented taxonomy**, and published —
 * > entirely through the UI, no SQL, no code change.
 *
 * "Invented" is the operative word: the point of the gate is that a category
 * nobody anticipated — *"illegal hoarding on a footpath"* — can be added by a
 * solutions engineer during a deployment call, and the classifier picks it up.
 * So the form takes a key, a name and an optional parent, and nothing else: the
 * other twelve fields on `TaxonomyNodeSpec` all have defaults the backend
 * declares, and asking for them here would turn a two-minute act into a form
 * somebody schedules.
 *
 * `router.refresh()` on success rather than a local list update — the tree
 * above is server-rendered from the control plane, and re-reading it is what
 * makes the gate *checkable*: the category appears because the server has it,
 * not because this component remembers submitting it.
 */
export function TaxonomyForm({ strings }: { readonly strings: Strings }) {
  const router = useRouter();
  const id = useId();
  const { state, send } = useWrite();
  const [key, setKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [parent, setParent] = useState("");

  const ready = key.trim() !== "" && displayName.trim() !== "";

  return (
    <form
      className="control__form"
      onSubmit={(event) => {
        event.preventDefault();
        void send("/api/control/taxonomy", {
          key: key.trim(),
          display_name: displayName.trim(),
          ...(parent.trim() === "" ? {} : { parent_key: parent.trim() }),
        }).then(() => {
          router.refresh();
        });
      }}
    >
      <fieldset disabled={state.kind === "sending"}>
        <legend className="type-micro">{t(strings, "control.taxonomy.add")}</legend>

        <label className="type-caption" htmlFor={`${id}-key`}>
          {t(strings, "control.taxonomy.key")}
        </label>
        <input
          id={`${id}-key`}
          className="control__field type-mono-data"
          value={key}
          onChange={(event) => {
            setKey(event.target.value);
          }}
        />

        <label className="type-caption" htmlFor={`${id}-name`}>
          {t(strings, "control.taxonomy.name")}
        </label>
        <input
          id={`${id}-name`}
          className="control__field type-body"
          value={displayName}
          onChange={(event) => {
            setDisplayName(event.target.value);
          }}
        />

        <label className="type-caption" htmlFor={`${id}-parent`}>
          {t(strings, "control.taxonomy.parent")}
        </label>
        <input
          id={`${id}-parent`}
          className="control__field type-mono-data"
          value={parent}
          onChange={(event) => {
            setParent(event.target.value);
          }}
        />

        <button type="submit" className="control__action type-caption" disabled={!ready}>
          {t(strings, "control.taxonomy.add")}
        </button>
      </fieldset>

      <p className="type-caption" role="status">
        {state.kind === "done"
          ? t(strings, "control.taxonomy.added")
          : state.kind === "refused"
            ? notTranslatable(state.detail ?? "") || t(strings, "control.taxonomy.failed")
            : null}
      </p>
    </form>
  );
}

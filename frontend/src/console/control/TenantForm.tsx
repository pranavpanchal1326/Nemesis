"use client";

import { useId, useState } from "react";

import type { components } from "@/generated/api";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { detailOf, useWrite } from "./forms";

type ProvisioningResult = components["schemas"]["ProvisioningResult"];

/**
 * Provision a tenant — Phase 5's gate, the first half.
 *
 * `POST /control-plane/tenants` creates a tenant *and everything it needs, in
 * one transaction*, and it returns counts describing rows that already exist —
 * its own docstring is careful to say 201 rather than 202 for exactly that
 * reason. So the result is reported as a fact ("provisioned `pune-demo`,
 * taxonomy revision 1") rather than as "started".
 *
 * **A template rather than a pile of fields.** `ProvisioningRequest` can carry
 * a whole city — taxonomy, zones, departments, calendars, prompt sets, shifts,
 * translations — and a form with all of that is a form nobody completes. The
 * backend ships templates for precisely this; naming one is the difference
 * between provisioning a working tenant in a deployment call and provisioning
 * an empty one that somebody has to finish later.
 */
export function TenantForm({ strings }: { readonly strings: Strings }) {
  const id = useId();
  const { state, send } = useWrite();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("");

  const ready = slug.trim() !== "" && name.trim() !== "";
  const result = state.kind === "done" ? readResult(state.body) : null;

  return (
    <form
      className="control__form"
      onSubmit={(event) => {
        event.preventDefault();
        void send("/api/control/tenants", {
          tenant: { slug: slug.trim(), name: name.trim() },
          ...(template.trim() === "" ? {} : { template: template.trim() }),
        });
      }}
    >
      <fieldset disabled={state.kind === "sending"}>
        <legend className="type-micro">{t(strings, "control.tenants")}</legend>

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

        <label className="type-caption" htmlFor={`${id}-name`}>
          {t(strings, "control.tenant.name")}
        </label>
        <input
          id={`${id}-name`}
          className="control__field type-body"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
          }}
        />

        <label className="type-caption" htmlFor={`${id}-template`}>
          {t(strings, "control.tenant.template")}
        </label>
        <input
          id={`${id}-template`}
          className="control__field type-mono-data"
          value={template}
          onChange={(event) => {
            setTemplate(event.target.value);
          }}
        />

        <button type="submit" className="control__action type-caption" disabled={!ready}>
          {t(strings, "control.tenant.provision")}
        </button>
      </fieldset>

      <p className="type-caption" role="status">
        {result !== null
          ? t(strings, "control.tenant.provisioned", {
              slug: result.slug,
              revision: result.taxonomy_revision,
            })
          : state.kind === "refused"
            ? notTranslatable(state.detail ?? "") || t(strings, "control.tenant.failed")
            : null}
      </p>
    </form>
  );
}

/**
 * Narrow the response to the generated type.
 *
 * A cast would let this screen report a successful provisioning off a body it
 * never checked — and "the tenant exists" is exactly the claim that must not be
 * made on faith.
 */
function readResult(body: unknown): ProvisioningResult | null {
  if (typeof body !== "object" || body === null) return null;
  const candidate = body as Record<string, unknown>;
  return typeof candidate["slug"] === "string" &&
    typeof candidate["taxonomy_revision"] === "number" &&
    typeof candidate["tenant_id"] === "string"
    ? (candidate as unknown as ProvisioningResult)
    : null;
}

export { detailOf };

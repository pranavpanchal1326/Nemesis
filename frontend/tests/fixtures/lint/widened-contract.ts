// A positive fixture: this *must* fire `no-hand-written-contract`.
//
// An alias of a generated type is a name for the published contract. An alias
// that intersects one with extra fields is a *different shape wearing the
// published one's name* — which is the failure Law 2 is about, arriving by the
// route the refined guard could most easily have let through.
export type ComplaintResponse = components["schemas"]["ComplaintResponse"] & {
  localOnlyField: string;
};

declare const components: { schemas: Record<string, unknown> };

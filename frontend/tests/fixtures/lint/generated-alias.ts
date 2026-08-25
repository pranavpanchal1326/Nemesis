// A negative fixture: nothing here may fire `no-hand-written-contract`.
//
// Both lines below tripped the guard before it was refined, and both are
// legitimate. Naming a generated type is what execution-plan Law 2 asks for;
// importing one is not declaring anything at all. A guard whose false positives
// are handled by exemption comments is a guard that teaches readers to reach
// for the comment.
import { type Complaint, type WorkOrderResponse } from "./nowhere.ts";

export type ComplaintResponse = components["schemas"]["ComplaintResponse"];
export type Policy = components["schemas"]["PolicyDocument"];

export type Used = Complaint | WorkOrderResponse | ComplaintResponse | Policy;

// Referenced so the fixture does not need a real generated module.
declare const components: { schemas: Record<string, unknown> };

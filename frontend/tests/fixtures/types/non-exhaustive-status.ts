import type { ComplaintStatus } from "@/generated/enums";

type Register = "in-flight" | "settled" | "contested" | "degraded" | "flagged";

// §E26.1 — a view that omits a status is a defect. `flagged` is missing, which
// is exactly the member a UI is most likely to forget.
export function register(status: ComplaintStatus): Register {
  switch (status) {
    case "submitted":
    case "verifying":
    case "classified":
    case "clustered":
    case "scored":
    case "routed":
    case "in_progress":
    case "pending_verification":
      return "in-flight";
    case "resolved":
    case "closed":
      return "settled";
    case "disputed":
      return "contested";
    case "pending_classification":
      return "degraded";
  }
}

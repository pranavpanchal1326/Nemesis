import { Press } from "@/press/Press";
import { formatReceiptTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<Receipt>` — §E26, §E17.3, §9.1.
 *
 * > Deckled paper, mono id, chain hash, the append-only sentence.
 *
 * > Not a toast. A **document**: saveable, shareable, printed on stock with a
 * > deckled edge … *"This record cannot be edited. Corrections are added, never
 * > overwritten."*
 * >
 * > Nobody reads the hash. Everybody feels that this system keeps records.
 *
 * The press wraps it because a receipt is PAPER in §E5's three-material law,
 * and the text sits in the exempt layer because §E6.2 says type prints solid.
 * The deckle is not decoration: a print is a physical object and it has an
 * edge, and that edge is most of why this reads as a thing you were given
 * rather than a message that appeared.
 *
 * **`chainHash` is optional, and that is a finding rather than a design.** The
 * submission response publishes `complaint_id`, `status` and
 * `estimated_processing_time_seconds` — no hash. Rendering a placeholder where
 * the proof goes would be exactly the §E3.3 violation this product exists to
 * refuse, so when it is absent the row is simply not there. Execution-plan
 * defect #19.
 */
export interface ReceiptProps {
  readonly complaintId: string;
  readonly reportedAt: string;
  readonly strings: Strings;
  /** The entity's chain head. Absent until the submission response carries it. */
  readonly chainHash?: string;
}

export function Receipt({ complaintId, reportedAt, chainHash, strings }: ReceiptProps) {
  return (
    <Press surface="document" quality="reduced" seed={hashSeed(complaintId)} className="receipt">
      <article className="receipt__sheet">
        <h2 className="receipt__title type-heading">{t(strings, "receipt.title")}</h2>

        <dl className="receipt__fields">
          <dt className="type-micro">{t(strings, "receipt.id")}</dt>
          <dd className="type-mono-data">{notTranslatable(complaintId)}</dd>

          <dt className="type-micro">{t(strings, "receipt.reportedAt")}</dt>
          {/* Long form, with its year. A receipt may be printed and read a year
              later, and a document cannot abbreviate its own date. */}
          <dd className="type-doc">{formatReceiptTime(reportedAt, strings.locale)}</dd>

          {chainHash === undefined ? null : (
            <>
              <dt className="type-micro">{t(strings, "receipt.chainHash")}</dt>
              <dd className="type-mono-data receipt__hash">{notTranslatable(chainHash)}</dd>
            </>
          )}
        </dl>

        <p className="receipt__append-only type-doc">{t(strings, "receipt.appendOnly")}</p>
      </article>
    </Press>
  );
}

/**
 * A stable seed per receipt, so one citizen's receipt registers the same way
 * every time they open it. A print is a specific object, not a new one each
 * time you look — and a golden image needs the same sheet twice (§E24).
 */
function hashSeed(id: string): number {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 16;
}

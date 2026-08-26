import { upstream } from "@/server/upstream";

/**
 * The redacted image, and only the redacted image — §11.4, §22, ADR-0040.
 *
 * `review/media.py` is emphatic: *"A redacted artefact — the only image any
 * route serves"*, and the lookup goes through `submission_media` rather than
 * straight to the filesystem because the redacted root is one directory shared
 * by every tenant. Content-addressed storage deduplicates identical bytes by
 * design, so resolving a path from a hash alone would let any tenant fetch any
 * other tenant's photograph by guessing.
 *
 * This handler adds nothing to that rule and takes nothing away. It exists so
 * the browser can render the image without naming a tenant: the hash comes from
 * the queue item the server already sent, the tenant header is applied by
 * `upstream`, and the upstream's own 404 for a hash this tenant does not own
 * arrives here as a 404.
 *
 * **Streamed, not buffered.** A console screen shows several photographs at
 * once and a reviewer opens them all morning; reading each one fully into the
 * Node process before answering would hold every open image in memory for no
 * benefit.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ sha256: string }> },
): Promise<Response> {
  const { sha256 } = await context.params;

  const { response } = await upstream.GET("/api/v1/review/media/{redacted_sha256}", {
    params: { path: { redacted_sha256: sha256 } },
    parseAs: "stream",
  });

  if (!response.ok || response.body === null) {
    // No body, no detail. A distinguishable "exists but not yours" would tell a
    // caller that a hash is real on this deployment, which is the disclosure
    // `deps.py` refuses for tenant ids.
    return new Response(null, { status: response.status === 200 ? 502 : response.status });
  }

  return new Response(response.body, {
    status: 200,
    headers: {
      "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
      // Private and short. The bytes are a redacted photograph attached to a
      // report under review; a shared cache on a municipal network is not a
      // place for it, and immutability is not worth the risk of a stale
      // redaction outliving a re-blur.
      "Cache-Control": "private, max-age=60",
      // The image is served to be looked at, never downloaded and re-shared
      // from the console.
      "Content-Disposition": "inline",
    },
  });
}

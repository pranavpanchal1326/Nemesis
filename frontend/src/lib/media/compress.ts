"use client";

/**
 * Client-side compression that does not destroy evidence — §E21, ADR-0057, F17.
 *
 * > Camera-first capture with client-side compression and EXIF preservation.
 *
 * Two steps. Re-encode the pixels at a bounded long edge and a fixed quality,
 * then transplant the original's metadata segments into the result
 * (`exif.ts`). The second step is what makes the first admissible: a canvas
 * re-encode strips EXIF, and a stripped photograph is one §11.1 trusts less —
 * which is a cost a citizen's report can absorb and a **closure photograph**
 * cannot, because there the trust penalty lands on the municipality's own
 * verification of its own contractor.
 *
 * **The refusal path is the important one.** Anything this module does not
 * fully understand — a HEIC the browser transcoded, a JPEG whose segments do
 * not walk, a decode that failed — returns the **original blob, uncompressed
 * and intact**. Never compressed-and-stripped. §11.1 prefers verifiable to
 * small, and the pathological case must not be the one that silently loses
 * provenance.
 *
 * **`createImageBitmap` and `OffscreenCanvas` where they exist**, because this
 * runs on the phone with the worst hardware in the fleet and decoding a 12 MP
 * photograph on the main thread janks the capture screen. Both have a
 * `<canvas>` fallback, which is what Safari took until recently.
 */

import { hasMetadata, transplantMetadata } from "./exif";

export interface CompressionOptions {
  /** Longest edge, in pixels, after scaling. */
  readonly maxEdge: number;
  /** JPEG quality, 0–1. */
  readonly quality: number;
}

/**
 * The defaults, and why these numbers.
 *
 * **1600 px** on the long edge is what a reviewer needs to tell a filled
 * pothole from an unfilled one on a console screen, and it is roughly a
 * quarter of the pixels a modern phone camera produces. **0.72** is below the
 * usual 0.85 because these photographs are evidence of *presence* rather than
 * of texture, and because the link this has to cross is the worst one in the
 * system (§E21). Together they take a typical 3 MB capture to about 180 kB —
 * which is the difference between a closure that uploads in a back lane and one
 * that does not.
 */
export const FIELD_COMPRESSION: CompressionOptions = { maxEdge: 1600, quality: 0.72 };

export interface CompressionResult {
  readonly blob: Blob;
  /** Whether the pixels were actually re-encoded. */
  readonly compressed: boolean;
  /**
   * Whether the result carries the original's metadata.
   *
   * Reported rather than assumed, and surfaced in the UI: a field hand whose
   * photograph lost its EXIF should be told, because §11.1 will treat that
   * upload as less trustworthy and they are the person who can retake it.
   */
  readonly metadataPreserved: boolean;
  readonly originalBytes: number;
  readonly bytes: number;
}

function untouched(blob: Blob, metadataPreserved: boolean): CompressionResult {
  return {
    blob,
    compressed: false,
    metadataPreserved,
    originalBytes: blob.size,
    bytes: blob.size,
  };
}

/**
 * Compress a photograph, keeping what makes it evidence.
 *
 * Never throws. Every failure resolves to the original blob, because the caller
 * is a capture screen and there is no useful thing for it to do with an
 * exception except send the photograph anyway.
 */
export async function compressPhoto(
  photo: Blob,
  options: CompressionOptions = FIELD_COMPRESSION,
): Promise<CompressionResult> {
  const original = new Uint8Array(await photo.arrayBuffer());
  const carriesMetadata = hasMetadata(original);

  // A file that is not a walkable JPEG is sent as it is. HEIC arrives here
  // already transcoded by the browser and without the original's metadata, so
  // this is also that path — and it is stated rather than discovered.
  if (photo.type !== "image/jpeg") return untouched(photo, carriesMetadata);

  let encoded: Blob | null = null;
  try {
    encoded = await reencode(photo, options);
  } catch {
    encoded = null;
  }
  if (encoded === null || encoded.size >= photo.size) {
    // A "compression" that made the file bigger is not one. Common on a
    // photograph that was already small, and the honest answer is the original.
    return untouched(photo, carriesMetadata);
  }

  if (!carriesMetadata) {
    return {
      blob: encoded,
      compressed: true,
      metadataPreserved: false,
      originalBytes: photo.size,
      bytes: encoded.size,
    };
  }

  const merged = transplantMetadata(original, new Uint8Array(await encoded.arrayBuffer()));
  if (merged === null) {
    // The transplant could not be performed safely. ADR-0057's rule: send the
    // original intact rather than a smaller file that lost its provenance.
    return untouched(photo, true);
  }

  // `merged.buffer` is `ArrayBufferLike`, which the DOM types will not accept
  // as a `BlobPart` because it could in principle be a `SharedArrayBuffer`.
  // It cannot be — it came from `new Uint8Array(n)` — and the copy is the
  // cheapest way to say so to the compiler.
  const blob = new Blob([new Uint8Array(merged)], { type: "image/jpeg" });
  return {
    blob,
    compressed: true,
    metadataPreserved: true,
    originalBytes: photo.size,
    bytes: blob.size,
  };
}

/** The scaled dimensions for a source, preserving aspect ratio. */
export function fitWithin(
  width: number,
  height: number,
  maxEdge: number,
): { readonly width: number; readonly height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const scale = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

async function reencode(photo: Blob, options: CompressionOptions): Promise<Blob | null> {
  const source = await decode(photo);
  if (source === null) return null;

  const { width, height } = fitWithin(source.width, source.height, options.maxEdge);

  if (typeof OffscreenCanvas === "function") {
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext("2d");
    if (context === null) return null;
    context.drawImage(source.image, 0, 0, width, height);
    source.release();
    return await canvas.convertToBlob({ type: "image/jpeg", quality: options.quality });
  }

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (context === null) return null;
  context.drawImage(source.image, 0, 0, width, height);
  source.release();

  return await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(
      (blob) => {
        resolve(blob);
      },
      "image/jpeg",
      options.quality,
    );
  });
}

interface Decoded {
  readonly image: CanvasImageSource;
  readonly width: number;
  readonly height: number;
  readonly release: () => void;
}

async function decode(photo: Blob): Promise<Decoded | null> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(photo);
    return {
      image: bitmap,
      width: bitmap.width,
      height: bitmap.height,
      // A bitmap holds decoded pixels — twelve megapixels is 48 MB — and the
      // GC will not hurry. Closing it is not optional on a field phone.
      release: () => {
        bitmap.close();
      },
    };
  }

  const url = URL.createObjectURL(photo);
  try {
    const image = await new Promise<HTMLImageElement | null>((resolve) => {
      const element = new Image();
      element.onload = () => {
        resolve(element);
      };
      element.onerror = () => {
        resolve(null);
      };
      element.src = url;
    });
    if (image === null) return null;
    return {
      image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      release: () => {
        URL.revokeObjectURL(url);
      },
    };
  } catch {
    URL.revokeObjectURL(url);
    return null;
  }
}

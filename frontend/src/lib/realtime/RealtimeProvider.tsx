"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api/complaints";
import { complaintKey } from "@/lib/api/queries";

import { startRealtimeBridge, type RealtimeEndpoint } from "./bridge";

/**
 * The React wiring around `bridge.ts` — §E14.2, §E14.3.
 *
 * Deliberately thin. Every behaviour worth asserting lives in the bridge, which
 * is a plain function with injected collaborators; this component's only job is
 * to own a query cache, start the bridge on mount, and stop it on unmount.
 *
 * **There is exactly one of these in the tree.** Two surfaces each starting a
 * bridge would double the hub's connection count per tab and replay the same
 * gap twice into one store.
 */

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Long, on purpose. Freshness on this product does not come from a
        // timer — it comes from the socket saying an entity changed, or from
        // §27.3's poll when the socket cannot. A short `staleTime` on top of
        // both would add a third, uncoordinated refetch schedule.
        staleTime: 60_000,
        // A read that fails is retried twice and then reported. The surfaces
        // render a named degradation rather than a spinner that never resolves
        // (§E13) — an infinite retry is how a broken deployment looks healthy.
        //
        // **Except where trying again cannot work.** `ApiError.retriable`
        // already draws that line for the submit path — 429 and 5xx will
        // plausibly succeed, a 404 will not — and the reads were ignoring it.
        // The visible cost was on §E17.4: a mistyped receipt id spent two
        // backoffs saying *"Reading the record…"* before admitting it could not
        // be read, so the screen's slowest, least certain state was the one a
        // typo produced. A definitive refusal is an answer, and this renders it
        // as one.
        retry: (failureCount, error) =>
          error instanceof ApiError ? error.retriable && failureCount < 2 : failureCount < 2,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export function RealtimeProvider({ children }: { readonly children: ReactNode }) {
  // Created once per mount and never during a child's render, so a Suspense
  // replay cannot produce two caches and two sets of in-flight requests.
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <RealtimeBridge />
      {children}
    </QueryClientProvider>
  );
}

/**
 * The effect, in its own component so nothing it does re-renders the tree the
 * provider wraps. It renders nothing: `<DegradedBanner>` belongs to a shell
 * that has a `Strings` in hand, and this module has none.
 */
function RealtimeBridge(): null {
  const queryClient = useQueryClient();

  useEffect(() => {
    // React 19 StrictMode mounts effects twice in development. The abort is
    // what makes the first mount's in-flight discovery a no-op rather than a
    // second socket the first cleanup has already forgotten about.
    const controller = new AbortController();

    const stop = startRealtimeBridge({
      discover: () => discoverEndpoint(controller.signal),
      refetch: async (entityId) => {
        // Prefix-keyed, so one call reaches the projection *and* the ledger.
        // `refetchQueries` rather than `invalidateQueries`: invalidation only
        // acts on an active observer, and the entity that just changed is often
        // behind a tab the reader is about to return to.
        await queryClient.refetchQueries({ queryKey: complaintKey(entityId) });
      },
      refetchAll: () => {
        void queryClient.invalidateQueries();
      },
    });

    return () => {
      controller.abort();
      stop();
    };
  }, [queryClient]);

  return null;
}

async function discoverEndpoint(signal: AbortSignal): Promise<RealtimeEndpoint | null> {
  try {
    const response = await fetch("/api/realtime", { cache: "no-store", signal });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    return isEndpoint(body) ? body : null;
  } catch {
    // Including the abort on unmount. A failed discovery has the same outcome
    // as an unavailable hub, and the bridge handles both.
    return null;
  }
}

function isEndpoint(value: unknown): value is RealtimeEndpoint {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as RealtimeEndpoint).available === "boolean"
  );
}

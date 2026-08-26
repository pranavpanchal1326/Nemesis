"use client";

import { useStore } from "zustand/react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { t, type Strings } from "@/lib/i18n/strings";
import { RealtimeProvider } from "@/lib/realtime/RealtimeProvider";
import { realtimeStore } from "@/lib/realtime/store";

/**
 * The film's one client boundary — the same shape `<CitizenShell>` has, and for
 * the same two reasons.
 *
 * **The film needs the transport, and it needs it mounted exactly once.** Act 4
 * is the citizen loop's own `<ReportFlow>`, which submits through a mutation and
 * reads a complaint's ledger through a query; Act 5 is M5's theatre reading the
 * same cache; Act 6 listens to the realtime bus. All three need a
 * `QueryClientProvider` and a running bridge above them, and
 * `RealtimeProvider`'s own note is explicit that there is exactly one in the
 * tree — two would double the hub's connection count per tab and replay the
 * same gap twice into one store.
 *
 * Found by running it: the film mounted `<ReportFlow>` with no provider above
 * it and Act 4 threw *"No QueryClient set"* on the server render, which took
 * the whole landing page down. The lesson is the one §E26 keeps making — a
 * component reused across surfaces brings its dependencies with it, and the
 * surface has to supply them rather than assume the last one did.
 *
 * **The banner belongs to whoever holds the strings.** `Degradation.cause` is a
 * locale key rather than a sentence (§E10.1, and Phase 18's gate), the store has
 * no `Strings`, and this does.
 */
export function StoryShell({
  strings,
  children,
}: {
  readonly strings: Strings;
  readonly children: React.ReactNode;
}) {
  return (
    <RealtimeProvider>
      <TransportBanner strings={strings} />
      {children}
    </RealtimeProvider>
  );
}

function TransportBanner({ strings }: { readonly strings: Strings }) {
  const degradation = useStore(realtimeStore, (state) => state.degradation);
  const transport = useStore(realtimeStore, (state) => state.transport);

  // Nothing to say while the socket is delivering, and nothing during the
  // first handshake: a banner that flashes on every page load teaches people to
  // ignore it, which is the one failure mode a degradation notice cannot
  // survive.
  if (degradation === null) return null;
  if (transport === "open") return null;

  return (
    <DegradedBanner
      cause={t(strings, degradation.cause)}
      since={new Date(degradation.since)}
      strings={strings}
    />
  );
}

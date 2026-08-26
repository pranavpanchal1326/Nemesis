"use client";

import Link from "next/link";
import { createContext, useContext } from "react";
import { useStore } from "zustand/react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { t, type Strings } from "@/lib/i18n/strings";
import { RealtimeProvider } from "@/lib/realtime/RealtimeProvider";
import { realtimeStore } from "@/lib/realtime/store";
import { SoundProvider } from "@/sound/SoundControl";

import "./citizen.css";

/**
 * The citizen surface's one client boundary — A5's mount point, and the only
 * place `<DegradedBanner>` can live.
 *
 * Two jobs, and they are here together because they are the same job seen from
 * two ends. `<RealtimeProvider>` starts the transport; this renders what the
 * transport says about itself. Splitting them would mean either a second store
 * subscription somewhere with no provider above it, or a banner that has to be
 * remembered on every screen.
 *
 * **The banner reads a key and resolves it here.** `Degradation.cause` is a
 * locale key, never a sentence — §E10.1, and Phase 18's gate that a locale
 * added in the control plane appears with no code change. The store has no
 * `Strings`; this does.
 */
export function CitizenShell({
  strings,
  children,
}: {
  readonly strings: Strings;
  readonly children: React.ReactNode;
}) {
  return (
    <RealtimeProvider>
      {/* §E12's sound layer. One per surface: it follows the bus, so the merge
          cue plays when this citizen's report is deduplicated — which is the
          one place in the product where that event is *about the person
          looking at it*. There is no unmute control on this surface, and that
          is deliberate: a citizen filing a report in thirty seconds is not who
          §E12's affordance is for, and the preference persists across surfaces
          for anybody who has set it. */}
      <SoundProvider />
      <TransportBanner strings={strings} />
      {/* The way back to the resident's door — §E17's surfaces are a camera and
          a ledger, and neither of them is a place to *start*. */}
      <nav className="citizen__ways type-micro" aria-label={t(strings, "story.ways")}>
        <Link href="/citizen">{t(strings, "portal.citizen.title")}</Link>
      </nav>
      <StringsContext.Provider value={strings}>{children}</StringsContext.Provider>
    </RealtimeProvider>
  );
}

function TransportBanner({ strings }: { readonly strings: Strings }) {
  const degradation = useStore(realtimeStore, (state) => state.degradation);
  const transport = useStore(realtimeStore, (state) => state.transport);

  // Nothing to say while the socket is delivering, and nothing to say during
  // the first handshake either: a banner that flashes on every page load
  // teaches people to ignore it, which is the one failure mode a degradation
  // notice cannot survive.
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

/**
 * Resolved strings, for the screens below.
 *
 * A context rather than a prop drilled through five components, and it holds a
 * value the server produced — so there is still exactly one place strings are
 * resolved, and it is still the server.
 */
const StringsContext = createContext<Strings | null>(null);

export function useStrings(): Strings {
  const strings = useContext(StringsContext);
  if (strings === null) {
    // A screen rendered outside the shell has no locale, and rendering it in
    // the source language would hide that — the next Marathi deployment would
    // find out from a citizen. Failing loudly is the smaller cost.
    throw new Error("useStrings() called outside <CitizenShell>");
  }
  return strings;
}

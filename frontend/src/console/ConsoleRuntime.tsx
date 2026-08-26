"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useStore } from "zustand/react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { t, type Strings } from "@/lib/i18n/strings";
import { RealtimeProvider } from "@/lib/realtime/RealtimeProvider";
import { realtimeStore } from "@/lib/realtime/store";
import { SoundProvider } from "@/sound/SoundControl";

/**
 * The console's one client boundary — §E19, §E13, §E14.3.
 *
 * Everything in `<ConsoleShell>` above this is a server component, and stays
 * one: the locale is negotiated on the server for the reasons
 * `src/server/strings.ts` gives, and a console shell that resolved its own
 * strings would ship the wrong `lang` to a screen reader on every Devanagari
 * deployment.
 *
 * Three things need a client, and they are here together because they are all
 * "the console, running":
 *
 *   · `<RealtimeProvider>` — one bridge per tab, per its own docstring. It is
 *     mounted at the shell rather than per screen so that moving between the
 *     queue and the policy studio does not tear down and re-open the socket.
 *   · `<DegradedBanner>` — §E19's shell-level mount. `Degradation.cause` is a
 *     locale key and the store has no `Strings`; this does.
 *   · The `Strings` context, so a client screen below can resolve words without
 *     every server page drilling the object through four components.
 *
 * `children` arrives as server-rendered nodes passed *through* a client
 * component, which keeps the screens themselves on the server.
 */
export function ConsoleRuntime({
  strings,
  children,
}: {
  readonly strings: Strings;
  readonly children: ReactNode;
}) {
  return (
    <RealtimeProvider>
      <SoundProvider />
      <StringsContext.Provider value={strings}>
        <TransportBanner strings={strings} />
        {children}
      </StringsContext.Provider>
    </RealtimeProvider>
  );
}

/**
 * What the transport says about itself.
 *
 * The same three-line rule the citizen shell uses, and deliberately the same:
 * §E26's whole argument for `<DegradedBanner>` is that a reader should not have
 * to learn two visual languages for "something is running in a reduced mode".
 * An officer who has seen this banner on the tracking page must recognise it on
 * the queue.
 */
function TransportBanner({ strings }: { readonly strings: Strings }) {
  const degradation = useStore(realtimeStore, (state) => state.degradation);
  const transport = useStore(realtimeStore, (state) => state.transport);

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

const StringsContext = createContext<Strings | null>(null);

/** Resolved strings, for a client screen below the shell. */
export function useConsoleStrings(): Strings {
  const strings = useContext(StringsContext);
  if (strings === null) {
    // Rendering the source language instead would hide the mistake until a
    // Marathi officer found it. Failing loudly is the smaller cost — the same
    // trade `useStrings()` makes on the citizen surface.
    throw new Error("useConsoleStrings() called outside <ConsoleShell>");
  }
  return strings;
}

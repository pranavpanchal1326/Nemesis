"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useStore } from "zustand/react";

import { realtimeStore, type TransportState } from "@/lib/realtime/store";

import {
  fetchComplaint,
  fetchComplaintHistory,
  type Complaint,
  type ComplaintHistory,
} from "./complaints";

/**
 * The read cache, and §27.3's polling fallback made into a behaviour — A4.
 *
 * **What was wrong before this file.** `transport: "polling"` existed in the
 * store and nothing polled. A refused upgrade therefore degraded to *nothing
 * updating at all*, behind a banner saying the saved state was being refreshed
 * every few seconds. The banner was honest about the intent and wrong about the
 * fact, which is worse than either — §6 Principle #8 fails harder when the UI
 * is confidently wrong than when it says nothing.
 *
 * **Why `refetchInterval` rather than a poller.** §27.3's fallback is *"poll the
 * read path"*, and the read path is per complaint. A standalone polling loop
 * would need its own registry of which entities are interesting, which is a
 * second answer to a question mounting a query already answers. Nothing on
 * screen means nothing to poll, and that is correct rather than a gap.
 *
 * The interval is five seconds because `nemesis/api/v1/complaints.py` sets
 * `Cache-Control: private, max-age=5` on that endpoint and says why: *"longer
 * would make the fallback visibly laggier than the WebSocket it replaces;
 * shorter would defeat the conditional request entirely."* Two numbers, one
 * decision, and this comment exists so nobody changes one of them.
 */

/** §27.3. Matched to the read path's own `max-age`. */
export const POLL_INTERVAL_MS = 5_000;

/**
 * Transports in which the socket is not delivering and the read path is the
 * only way to learn anything.
 *
 * `reconnecting` is in the set on purpose. Backoff reaches fifteen seconds, and
 * fifteen seconds of a frozen screen after a dropped connection is the failure
 * the fallback exists for. Polling through a reconnect is nearly free — the
 * request is conditional and answers 304 from one indexed lookup.
 *
 * `connecting` and `idle` are not: they are the first few hundred milliseconds
 * of page life, and polling there would race the handshake for no benefit.
 */
const POLLING_TRANSPORTS: ReadonlySet<TransportState> = new Set<TransportState>([
  "polling",
  "reconnecting",
]);

export function isPollingTransport(transport: TransportState): boolean {
  return POLLING_TRANSPORTS.has(transport);
}

/**
 * Query keys, prefix-structured so the reconciler can refetch *everything about
 * one entity* with one call.
 *
 * `["complaint", id]` is a prefix of `["complaint", id, "history"]`, which is
 * what makes `refetchQueries({ queryKey: complaintKey(id) })` reach both the
 * projection and the ledger. §E14.3's rule is that an event is a hint about an
 * entity, not about a view of it.
 */
export const complaintKey = (complaintId: string) => ["complaint", complaintId] as const;
export const complaintHistoryKey = (complaintId: string) =>
  ["complaint", complaintId, "history"] as const;

function usePollInterval(): number | false {
  const transport = useStore(realtimeStore, (state) => state.transport);
  return isPollingTransport(transport) ? POLL_INTERVAL_MS : false;
}

/**
 * One complaint's current state, kept current by whichever transport is working.
 *
 * When the socket is open this refetches only when §E14.3's reconciler says the
 * entity changed. When it is not, this polls. The component does not know which,
 * which is the point: a surface should not contain a branch on how it is being
 * told things.
 */
export function useComplaint(
  complaintId: string,
  /** False before an id exists. A submission is optimistic (§E17.1), so the
   *  screen renders for a moment with nothing to read — and a query against an
   *  empty id would be a guaranteed 404 on the surface a citizen sees first. */
  enabled = true,
): UseQueryResult<Complaint> {
  const refetchInterval = usePollInterval();
  return useQuery({
    queryKey: complaintKey(complaintId),
    queryFn: ({ signal }) => fetchComplaint(complaintId, signal),
    enabled: enabled && complaintId !== "",
    refetchInterval,
    // The interval keeps running while the tab is hidden only if we say so, and
    // we do not: a backgrounded tab polling every five seconds is a phone
    // spending battery on a screen nobody is looking at. React Query refetches
    // on focus, so the first thing a returning reader sees is current.
    refetchIntervalInBackground: false,
  });
}

/**
 * §E17.4's ledger, and §E17.3's live chain head.
 *
 * Polled on the same schedule for the same reason. It is the more expensive of
 * the two reads and the one whose staleness is most visible — a ledger that
 * stops growing while a report is being worked on is the *"'In Progress' is the
 * enemy"* screen §E17.4 is written against.
 */
export function useComplaintHistory(complaintId: string): UseQueryResult<ComplaintHistory> {
  const refetchInterval = usePollInterval();
  return useQuery({
    queryKey: complaintHistoryKey(complaintId),
    queryFn: ({ signal }) => fetchComplaintHistory(complaintId, signal),
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

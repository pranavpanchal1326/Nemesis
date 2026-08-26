"use client";

import { t, type Strings } from "@/lib/i18n/strings";

import { FieldCapture } from "./FieldCapture";
import { useHere } from "./here";
import { Jobs } from "./Jobs";
import { QueueList } from "./QueueList";

/**
 * The field app, in one screen — §E21, F17.
 *
 * Three sections, in the order a field hand meets them: what to do, the camera,
 * and what has not gone yet. No navigation, no tabs, and no board (§E21: *field
 * staff never see a kanban*) — a person with one glove off and a phone in
 * sunlight gets a single scroll.
 *
 * A client component because all three of its parts are: a geolocation watch, a
 * camera input, and an IndexedDB subscription. The words came from the server
 * (`layout.tsx`), so the first paint is in the right language and script.
 */
export function FieldScreen({
  strings,
  locale,
}: {
  readonly strings: Strings;
  readonly locale: string;
}) {
  const here = useHere();

  return (
    <main className="field__main">
      <Jobs strings={strings} />

      <FieldCapture
        strings={strings}
        locale={locale}
        coordinates={here === null ? null : { latitude: here.latitude, longitude: here.longitude }}
      />

      {here === null ? null : (
        // §E3.3 — the accuracy is shown rather than smoothed away. A fix good to
        // sixty metres is a different claim from one good to five, and the
        // person standing there is the only one who can decide whether to wait.
        <p className="field__fix type-mono-data">
          {t(strings, "field.accuracy", { metres: Math.round(here.accuracyMetres) })}
        </p>
      )}

      <QueueList strings={strings} />
    </main>
  );
}

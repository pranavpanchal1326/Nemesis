/**
 * §E19 — "The Light Table". Dark-first, keyboard-first, dense, printable.
 * The role determines the shell, not just the permissions (§E19.0, Phase 13).
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <div data-surface="console">{children}</div>;
}

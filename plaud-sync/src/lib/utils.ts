export type Theme = "system" | "light" | "dark";

/** Apply the chosen theme by setting `data-theme` on <html>. "system" clears it
 * so the prefers-color-scheme media query takes over. */
export function applyTheme(theme: Theme | string | undefined): void {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") {
    root.dataset.theme = theme;
  } else {
    delete root.dataset.theme;
  }
}

export function formatDuration(ms: number): string {
  if (!ms) return "—";
  const minutes = Math.max(1, Math.round(ms / 60_000));
  return `${minutes} min`;
}

export function formatDate(timestampMs: number): string {
  if (!timestampMs) return "Unknown date";
  return new Date(timestampMs).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
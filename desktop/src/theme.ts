import { useEffect, useState } from "react";

/**
 * Day / Night theme. The choice is a per-computer preference kept in
 * localStorage (shared by the main and Ctrl+Space windows, which load the
 * same origin); "system" follows Windows' light or dark setting. The
 * resolved theme is written to <html data-theme>, which App.css reads.
 */
export type ThemeChoice = "system" | "day" | "night";
const KEY = "intellifile.theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");

export function getThemeChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "day" || v === "night" || v === "system") return v;
  } catch { /* storage unavailable: fall back to the system setting */ }
  return "system";
}

export function resolvedTheme(choice: ThemeChoice = getThemeChoice()): "day" | "night" {
  return choice === "system" ? (media.matches ? "night" : "day") : choice;
}

export function applyTheme(choice: ThemeChoice = getThemeChoice()): void {
  document.documentElement.dataset.theme = resolvedTheme(choice) === "night" ? "dark" : "light";
}

const listeners = new Set<(c: ThemeChoice) => void>();

export function setThemeChoice(choice: ThemeChoice): void {
  try { localStorage.setItem(KEY, choice); } catch { /* still applied for this session */ }
  applyTheme(choice);
  listeners.forEach((l) => l(choice));
}

/** Call once at start-up: applies the saved choice and keeps it in step with Windows and the other window. */
export function initTheme(): void {
  applyTheme();
  media.addEventListener("change", () => { if (getThemeChoice() === "system") applyTheme(); });
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) { applyTheme(); listeners.forEach((l) => l(getThemeChoice())); }
  });
}

export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(getThemeChoice);
  useEffect(() => {
    listeners.add(setChoice);
    return () => { listeners.delete(setChoice); };
  }, []);
  return [choice, setThemeChoice];
}

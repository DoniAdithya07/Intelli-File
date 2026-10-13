/**
 * Test switch: `?uitest=1` (or VITE_UI_TEST=1). Automated UI checks must never launch a real file
 * (docs: Explorer UI spec, T1/T2), so with the switch on, opening and revealing only record
 * "would open: <path>" in window.__uitest and show nothing on the desktop.
 */
export const UITEST: boolean = new URLSearchParams(window.location.search).get("uitest") === "1" || import.meta.env.VITE_UI_TEST === "1";

export interface UiCall { kind: "open" | "reveal"; path: string }
export function recordUiCall(call: UiCall): void {
  const w = window as unknown as { __uitest?: UiCall[] };
  (w.__uitest ??= []).push(call);
}

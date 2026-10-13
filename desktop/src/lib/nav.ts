/** Where the user is. A tiny router: one value, plus a history stack kept by App. */
export type Location =
  | { kind: "home" }
  | { kind: "thispc" }
  | { kind: "folder"; path: string }
  | { kind: "search"; query?: string; folder?: string | null; run?: number }
  | { kind: "photos"; query?: string; run?: number }
  | { kind: "ask" | "foryou" | "activity" | "index" | "settings" | "help" };

export type PageKind = Location["kind"];

export function sameLocation(a: Location, b: Location): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export const PAGE_TITLES: Record<string, string> = {
  home: "Home", thispc: "This PC", search: "Search", photos: "Photos", ask: "Ask", foryou: "For You", activity: "Activity", index: "Index", settings: "Settings", help: "Help",
};

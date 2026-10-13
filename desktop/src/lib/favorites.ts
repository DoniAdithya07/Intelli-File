import { useEffect, useState } from "react";

/** Favorites and pinned folders: kept on this computer only (localStorage), keyed by the full path. */
export interface Fav { path: string; name: string; isDir: boolean; added: number }
const KEYS = { fav: "intellifile.favorites", pin: "intellifile.pinned" } as const;
type Which = keyof typeof KEYS;
const listeners = new Set<() => void>();

function read(which: Which): Fav[] {
  try { const v = JSON.parse(localStorage.getItem(KEYS[which]) ?? "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
}
function write(which: Which, list: Fav[]) {
  try { localStorage.setItem(KEYS[which], JSON.stringify(list)); } catch { /* kept for this session only */ }
  listeners.forEach((l) => l());
}
export function toggleFav(which: Which, item: { path: string; name: string; isDir: boolean }) {
  const list = read(which);
  const at = list.findIndex((f) => f.path.toLowerCase() === item.path.toLowerCase());
  write(which, at >= 0 ? list.filter((_, i) => i !== at) : [...list, { ...item, added: Date.now() / 1000 }]);
}
export function useFavs(which: Which): Fav[] {
  const [list, setList] = useState(() => read(which));
  useEffect(() => {
    const l = () => setList(read(which));
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, [which]);
  return list;
}

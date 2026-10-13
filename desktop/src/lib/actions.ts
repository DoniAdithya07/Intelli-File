import { useCallback, useMemo } from "react";
import { forgetFolder, indexFile, indexFolder, reindexFolder } from "../backend";
import { toggleFav, useFavs } from "./favorites";
import { Location } from "./nav";
import { samePath } from "./format";
import { canOpen, openResult, revealResult } from "../ui/ResultCard";
import type { MenuItem } from "../ui/controls";

/** What the actions need to know about one file or folder. */
export interface ActEntry { path: string; name: string; is_dir: boolean; indexed: boolean; file_id?: string | null }
export type Notify = (message: string | null, action?: { label: string; onClick: () => void }) => void;

/**
 * Everything a row can do, in one place. Opening a file goes through one function (`openResult`, which asks the
 * desktop side to open it) and only for files IntelliFile has indexed: for any other file the answer is a
 * message with "Show in Explorer", never a launch.
 */
export function useActions(onNavigate: (l: Location) => void, notify: Notify, roots: string[], onIndexChanged: () => void) {
  const favs = useFavs("fav");
  const pins = useFavs("pin");

  const reveal = useCallback((e: ActEntry) => revealResult(e.path, (m) => notify(m), e.file_id), [notify]);
  const open = useCallback((e: ActEntry) => {
    if (e.is_dir) { onNavigate({ kind: "folder", path: e.path }); return; }
    if (e.indexed && canOpen(e.path)) { openResult(e.path, (m) => notify(m, { label: "Show in Explorer", onClick: () => reveal(e) }), e.file_id); return; }
    notify(e.indexed ? "IntelliFile opens documents, pictures, audio and video. Show this one in Explorer to open it there." : "IntelliFile only opens files it has indexed.", { label: "Show in Explorer", onClick: () => reveal(e) });
  }, [onNavigate, notify, reveal]);
  const copyPaths = useCallback((list: ActEntry[]) => {
    navigator.clipboard?.writeText(list.map((e) => e.path).join("\n")).catch(() => notify("The path could not be copied."));
  }, [notify]);

  const run = useCallback(async (job: Promise<{ error?: string }>) => {
    try { const r = await job; if (r.error) notify(r.error); onIndexChanged(); }
    catch (e) { notify(e instanceof Error ? e.message : String(e)); }
  }, [notify, onIndexChanged]);

  const menu = useCallback((list: ActEntry[]): MenuItem[] => {
    if (!list.length) return [];
    const items: MenuItem[] = [];
    const one = list.length === 1 ? list[0] : null;
    if (one) {
      if (one.is_dir || (one.indexed && canOpen(one.path))) items.push({ id: "open", label: "Open", shortcut: "Enter", onClick: () => open(one) });
      items.push({ id: "reveal", label: "Show in Explorer", shortcut: "Ctrl+Enter", onClick: () => reveal(one) });
    }
    items.push({ id: "copy", label: list.length > 1 ? "Copy paths" : "Copy path", shortcut: "Ctrl+Shift+C", onClick: () => copyPaths(list) });
    items.push({ id: "sep1", sep: true });
    const allFav = list.every((e) => favs.some((f) => samePath(f.path, e.path)));
    items.push({ id: "fav", label: allFav ? "Remove from Favorites" : "Add to Favorites", onClick: () => list.forEach((e) => { if (allFav === favs.some((f) => samePath(f.path, e.path))) toggleFav("fav", { path: e.path, name: e.name, isDir: e.is_dir }); }) });
    if (one?.is_dir) {
      const pinned = pins.some((p) => samePath(p.path, one.path));
      items.push({ id: "pin", label: pinned ? "Unpin from the left list" : "Pin to the left list", onClick: () => toggleFav("pin", { path: one.path, name: one.name, isDir: true }) });
      const isRoot = roots.some((r) => samePath(r, one.path));
      items.push({ id: "sep2", sep: true });
      if (!one.indexed) items.push({ id: "index", label: "Index this folder", onClick: () => run(indexFolder(one.path)) });
      if (isRoot) {
        items.push({ id: "reindex", label: "Re-index this folder", onClick: () => run(reindexFolder(one.path)) });
        items.push({ id: "forget", label: "Remove from index", onClick: () => run(forgetFolder(one.path)) });
      }
    } else if (one && !one.indexed) {
      items.push({ id: "sep2", sep: true });
      items.push({ id: "indexfile", label: "Index this file", onClick: () => run(indexFile(one.path)) });
    }
    return items;
  }, [favs, pins, roots, open, reveal, copyPaths, run]);

  return useMemo(() => ({ open, reveal, copyPaths, menu, run }), [open, reveal, copyPaths, menu, run]);
}

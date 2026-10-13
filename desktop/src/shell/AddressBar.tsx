import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { ApiError, browseList } from "../backend";
import { pathSegments } from "../lib/format";
import { Location, PAGE_TITLES } from "../lib/nav";
import { Fi, G } from "../ui/controls";

const PAGE_ICON: Record<string, string> = { home: G.home, thispc: G.thispc, search: G.search, photos: G.picture, ask: G.chat, foryou: G.person, activity: G.history, index: G.folder, settings: G.settings, help: G.help };

interface Props {
  location: Location;
  /** Names for drive roots, e.g. "C:" -> "OS (C:)". */
  driveNames: Record<string, string>;
  canBack: boolean;
  canForward: boolean;
  canUp: boolean;
  onBack: () => void;
  onForward: () => void;
  onUp: () => void;
  onRefresh: () => void;
  onNavigate: (l: Location) => void;
  /** Bump to open the field for typing (Ctrl+L). */
  editSignal: number;
}

/** Back, forward, up, refresh and the breadcrumb: click a part to go there, or click the empty part to type a path. */
export function AddressBar({ location, driveNames, canBack, canForward, canUp, onBack, onForward, onUp, onRefresh, onNavigate, editSignal }: Props) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [bad, setBad] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const trail = useRef<HTMLOListElement>(null);
  // A path too long for the field shows its end, which is where you are.
  useEffect(() => { if (trail.current) trail.current.scrollLeft = trail.current.scrollWidth; });
  const editable = location.kind === "folder" || location.kind === "home" || location.kind === "thispc" || location.kind === "search";
  const current = location.kind === "folder" ? location.path : location.kind === "search" ? location.folder ?? "" : "";

  // Clicking edits only where a path makes sense; Ctrl+L works on every page.
  const start = (force = false) => { if (!editable && !force) return; setText(current); setBad(null); setEditing(true); };
  useEffect(() => { if (editSignal > 0) start(true); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [editSignal]);
  useEffect(() => { if (editing) { input.current?.focus(); input.current?.select(); } }, [editing]);
  useEffect(() => { setEditing(false); setBad(null); }, [location]);

  async function go() {
    const path = text.trim();
    if (!path) { setEditing(false); return; }
    try { await browseList(path, 0, 1); onNavigate({ kind: "folder", path }); setEditing(false); }
    catch (e) { setBad(e instanceof ApiError && e.status === 403 ? "You don't have permission to open this folder." : e instanceof ApiError && e.status === 404 ? "That folder wasn't found." : e instanceof ApiError ? "That isn't a folder IntelliFile can open." : "The search engine isn't running, so folders can't be checked."); }
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); go(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(false); }
  };

  const crumbs: { name: string; loc?: Location }[] = [];
  const addFolder = (path: string) => pathSegments(path).forEach((s, i) => crumbs.push({ name: i === 0 ? driveNames[s.name.toUpperCase()] ?? s.name : s.name, loc: { kind: "folder", path: s.path } }));
  if (location.kind === "folder") addFolder(location.path);
  else if (location.kind === "search" && location.query) {
    if (location.folder) addFolder(location.folder);
    crumbs.push({ name: `Search results for "${location.query}"` });
  } else crumbs.push({ name: PAGE_TITLES[location.kind] ?? "" });
  const icon = location.kind === "folder" ? G.folder : PAGE_ICON[location.kind];

  const nav = (label: string, g: string, on: () => void, enabled: boolean, key?: string) => (
    <button className="cmd" aria-label={label} title={key ? `${label} (${key})` : label} disabled={!enabled} onClick={on}><Fi g={g} /></button>
  );

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1">
      {nav("Back", G.back, onBack, canBack, "Alt+Left")}
      {nav("Forward", G.forward, onForward, canForward, "Alt+Right")}
      {nav("Up", G.up, onUp, canUp, "Alt+Up")}
      {nav("Refresh", G.refresh, onRefresh, true, "F5")}
      <div className="relative min-w-0 flex-1">
        <div
          role="navigation"
          aria-label="Address"
          className={`flex h-8 min-w-0 items-center gap-1 rounded border bg-field px-2 ${bad ? "border-error" : "border-rule-strong"} ${editable ? "cursor-text" : ""}`}
          onClick={(e) => { if (e.target === e.currentTarget) start(); }}
        >
          {editing ? (
            <input ref={input} aria-label="Folder path" aria-invalid={!!bad} aria-describedby={bad ? "addr-error" : undefined} className="min-w-0 flex-1 bg-transparent text-[14px] outline-none" spellCheck={false}
              value={text} onChange={(e) => { setText(e.target.value); setBad(null); }} onKeyDown={onKey} onBlur={() => setEditing(false)} />
          ) : (
            <>
              <span className="grid h-6 w-6 shrink-0 place-items-center text-sub"><Fi g={icon} /></span>
              <ol ref={trail} className="flex min-w-0 flex-1 items-center justify-start gap-0.5 overflow-hidden whitespace-nowrap" onClick={(e) => { if (e.target === e.currentTarget) start(); }}>
                {crumbs.map((c, i) => (
                  <li key={i} className="flex shrink-0 items-center gap-0.5">
                    {i > 0 && <Fi g={G.right} size={10} className="text-dim" />}
                    {c.loc && i < crumbs.length - 1 ? (
                      <button className="h-6 truncate rounded px-1.5 text-[14px] hover:bg-[var(--hover)]" onClick={() => onNavigate(c.loc!)}>{c.name}</button>
                    ) : (
                      <span className="truncate px-1.5 text-[14px]" aria-current="page" onClick={() => start()}>{c.name}</span>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
        {bad && <div id="addr-error" role="alert" className="absolute left-0 top-full z-30 mt-1 rounded bg-error-soft px-2 py-1 text-[12px] text-error">{bad}</div>}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, describeError, engineUnreachable, fileKind, getRecommendations, listEvents, Recommendation, recordEvent, RouteReport, search, SearchResult, suggest, whyMissing } from "../backend";
import { baseName, dateText, sizeText } from "../lib/format";
import { useStatusBar } from "../shell/ctx";
import { Banner, CommandBar, CommandButton, EmptyState, Fi, G, Pills } from "../ui/controls";
import { Column, FileTable, Item, Sort } from "../ui/FileTable";
import { LoadFailed, shortPlace } from "../ui/kit";
import { Highlighted, openResult, revealResult } from "../ui/ResultCard";
import { SearchPreview } from "../ui/SearchPreview";
import { FileIcon } from "../ui/kit";

// ---- kinds of file, filters and sort ----

type TabId = "all" | "document" | "image" | "audio";
const TABS: { id: TabId; label: string }[] = [
  { id: "all", label: "All" },
  { id: "document", label: "Documents" },
  { id: "image", label: "Images" },
  { id: "audio", label: "Audio" },
];
// File types offered in Filter > Type, per tab.
const TYPES: Record<Exclude<TabId, "all">, string[]> = {
  document: ["pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "html"],
  image: ["png", "jpg", "jpeg", "webp"],
  audio: ["mp3", "m4a", "wav", "flac"],
};
const SIZES: { id: string; label: string; tokens: string[] }[] = [
  { id: "", label: "Any size", tokens: [] },
  { id: "small", label: "Under 1 MB", tokens: ["size:<1mb"] },
  { id: "medium", label: "1 MB to 10 MB", tokens: ["size:>1mb", "size:<10mb"] },
  { id: "large", label: "Over 10 MB", tokens: ["size:>10mb"] },
];

interface Filters { type: string; after: string; before: string; size: string; folder: string }
const NO_FILTERS: Filters = { type: "", after: "", before: "", size: "", folder: "" };

/** The filter words the backend understands. They are never shown to the user: the chips say the same in plain words. */
function filterTokens(tab: TabId, f: Filters): string[] {
  const out: string[] = [];
  const type = f.type || (tab === "all" ? "" : tab);
  if (type) out.push(`type:${type}`);
  if (f.after) out.push(`after:${f.after}`);
  if (f.before) out.push(`before:${f.before}`);
  out.push(...(SIZES.find((s) => s.id === f.size)?.tokens ?? []));
  // in: takes one word (the backend splits on spaces): keep the longest.
  const folderWord = f.folder.trim().split(/\s+/).sort((a, b) => b.length - a.length)[0];
  if (folderWord) out.push(`in:${folderWord}`);
  return out;
}

type SortCol = "relevance" | "name" | "modified" | "size";
function sortGroup(list: SearchResult[], col: SortCol, dir: "asc" | "desc"): SearchResult[] {
  if (col === "relevance") return list; // the backend's order
  const d = dir === "asc" ? 1 : -1;
  const by = col === "name" ? (a: SearchResult, b: SearchResult) => a.filename.localeCompare(b.filename, undefined, { sensitivity: "base", numeric: true })
    : col === "size" ? (a: SearchResult, b: SearchResult) => a.size - b.size
      : (a: SearchResult, b: SearchResult) => a.modified_time - b.modified_time;
  return [...list].sort((a, b) => d * by(a, b));
}

const TIER_LABEL: Record<string, string> = {
  filename: "file name",
  metadata: "file details",
  keyword: "keywords",
  hybrid: "keywords + meaning",
  "hybrid+rerank": "keywords + meaning, then reranking",
};
const MAX_WEAK = 5;
const PERSONAL_PREFIXES = ["You use this", "You used to", "Your usual", "Your most-used"];

/** Why a file matched, in plain words (never a score or a stage name). */
function reasonOf(r: SearchResult): { why: string; personal: string | null } {
  const group = fileKind(r.filename).group;
  const first = r.why.find((w) => !PERSONAL_PREFIXES.some((p) => w.startsWith(p))) ?? "";
  const personal = r.why.find((w) => PERSONAL_PREFIXES.some((p) => w.startsWith(p))) ?? null;
  let why = "Words in the file";
  if (first.startsWith("Filename contains") || (!r.chunk_id && !first)) why = "File name";
  else if (group === "audio" && r.chunk_id) why = "Spoken words";
  else if (group === "image" && r.chunk_id) why = "Text in the picture";
  else if (first.startsWith("Matched based on meaning")) why = "Same meaning";
  return { why, personal };
}
function tabOf(r: SearchResult): Exclude<TabId, "all"> {
  const g = fileKind(r.filename).group;
  return g === "image" ? "image" : g === "audio" ? "audio" : "document";
}

// ---- Filter popover ----

function FiltersPopover({ tab, filters, onChange, onClose }: { tab: TabId; filters: Filters; onChange: (f: Filters) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.parentElement?.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    ref.current?.querySelector<HTMLElement>("select, input")?.focus();
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey, true); };
  }, [onClose]);
  const types = tab === "all" ? [...TYPES.document, ...TYPES.image, ...TYPES.audio] : TYPES[tab];
  const set = (k: keyof Filters) => (e: { currentTarget: { value: string } }) => onChange({ ...filters, [k]: e.currentTarget.value });
  const field = "mt-1 h-8 w-full rounded border border-rule-strong bg-field px-2 text-[14px] text-ink";
  return (
    <div ref={ref} role="dialog" aria-label="Filters" className="menu anim-pop absolute left-0 top-full z-30 mt-1 w-[300px] !p-4">
      <div className="grid grid-cols-2 gap-3 text-[12px] text-sub">
        <label className="col-span-2">Type
          <select className={field} value={filters.type} onChange={set("type")}>
            <option value="">{tab === "all" ? "Any type" : `Any ${TABS.find((t) => t.id === tab)!.label.toLowerCase().replace(/s$/, "")}`}</option>
            {types.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
          </select>
        </label>
        <label>Modified after<input type="date" className={field} value={filters.after} onChange={set("after")} /></label>
        <label>Modified before<input type="date" className={field} value={filters.before} onChange={set("before")} /></label>
        <label className="col-span-2">Size
          <select className={field} value={filters.size} onChange={set("size")}>
            {SIZES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <label className="col-span-2">Folder
          <input className={field} value={filters.folder} onChange={set("folder")} placeholder="A word from the folder's name" spellCheck={false} />
        </label>
      </div>
      <div className="mt-4 flex justify-between">
        <button className="btn-ghost h-8 px-2 text-[14px]" onClick={() => onChange(NO_FILTERS)}>Clear filters</button>
        <button className="btn-primary h-8 px-4 text-[14px]" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

// ---- before any search ----

const EXAMPLES = [
  // Each one is answered by the sample folder (checked against src-tauri/resources/sample-folder).
  { q: "notes about adding servers when traffic grows", why: "describe what it is about" },
  { q: "type:txt in:invoices euros", why: "narrow by type and folder" },
  { q: "? when is the march invoice due and how much", why: "ask a question, answered from your files" },
];

/** Search start: the searches you ran lately and files picked from how you work. */
function SearchHome({ onRun, onSeeAll, onError }: { onRun: (q: string) => void; onSeeAll: () => void; onError: (m: string) => void }) {
  const [recent, setRecent] = useState<string[] | null>(null);
  const [picks, setPicks] = useState<Recommendation[]>([]);
  const [recentFailed, setRecentFailed] = useState(false);
  const [picksFailed, setPicksFailed] = useState(false);
  useEffect(() => {
    listEvents(200).then((r) => {
      const seen: string[] = [];
      for (const e of r.events) {
        const found = Number((e.meta as { count?: number } | null)?.count ?? 0) > 0;
        const q = e.kind === "query" && found ? e.query?.trim() : null;
        if (q && !seen.some((x) => x.toLowerCase() === q.toLowerCase())) seen.push(q);
        if (seen.length === 6) break;
      }
      setRecent(seen);
    }).catch((e) => { setRecent([]); setRecentFailed(!engineUnreachable(e)); });
    getRecommendations().then((r) => {
      if (!r.enabled) return;
      const all = [...r.likely_next, ...r.usual_now, ...r.recent];
      setPicks(all.filter((x, i) => all.findIndex((y) => y.file_id === x.file_id) === i).slice(0, 4));
    }).catch((e) => setPicksFailed(!engineUnreachable(e)));
  }, []);
  return (
    <div className="mx-auto max-w-[760px] space-y-6 px-6 py-6">
      <p className="text-[14px] text-sub">Find anything in the folders you indexed, by what it says or what it is about.</p>
      {recentFailed && <section><h2 className="text-[14px] font-semibold">Recent searches</h2><div className="mt-2"><LoadFailed what="your recent searches" /></div></section>}
      {recent && recent.length > 0 && (
        <section>
          <h2 className="text-[14px] font-semibold">Recent searches</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {recent.map((q) => <button key={q} className="pill max-w-[260px] truncate text-[14px]" onClick={() => onRun(q)}>{q}</button>)}
          </div>
        </section>
      )}
      {picksFailed && <section><h2 className="text-[14px] font-semibold">For You</h2><div className="mt-2"><LoadFailed what="files picked for you" /></div></section>}
      {picks.length > 0 && (
        <section>
          <div className="flex items-baseline justify-between">
            <h2 className="text-[14px] font-semibold">For You</h2>
            <button className="text-[14px] text-sub hover:text-ink hover:underline" onClick={onSeeAll}>See all</button>
          </div>
          <ul className="mt-2">
            {picks.map((r) => (
              <li key={r.file_id}>
                <button className="row flex h-12 w-full items-center gap-3 rounded px-2 text-left" onClick={() => { recordEvent("recommendation_clicked", { file_id: r.file_id, path: r.path }); openResult(r.path, onError, r.file_id); }}>
                  <FileIcon filename={r.filename} size={28} />
                  <span className="min-w-0 flex-1"><span className="block truncate text-[14px]">{r.filename}</span><span className="block truncate text-[12px] text-sub">{shortPlace(r.path)}</span></span>
                  <span className="max-w-[45%] shrink-0 truncate text-right text-[12px] text-sub">{r.reason}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {recent !== null && (
        <section>
          <h2 className="text-[14px] font-semibold">Try a search</h2>
          <ul className="mt-2">
            {EXAMPLES.map((ex) => (
              <li key={ex.q}>
                <button className="row flex h-10 w-full items-baseline gap-3 rounded px-2 text-left" onClick={() => onRun(ex.q)}>
                  <span className="text-[14px]">{ex.q}</span>
                  <span className="ml-auto shrink-0 text-[12px] text-sub">{ex.why}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// ---- the page ----

interface Props {
  query: string;
  /** Absolute folder path, or null for everywhere. */
  folder: string | null;
  /** Changes on every Enter, so the same words search again. */
  run: number;
  engineReady: boolean;
  folderCount: number;
  fileCount: number;
  onError: (m: string | null) => void;
  onAsk: (question: string) => void;
  onSeeAll: () => void;
  /** Search these words (from a suggestion or an example). */
  onRunQuery: (q: string) => void;
  /** Search the same words everywhere. */
  onEverywhere: () => void;
  onClear: () => void;
  preview: boolean;
  onPreview: (b: boolean) => void;
  voiceNote: { forQuery: string; heard: string | null; snap: string | null } | null;
}

export function SearchPage({ query, folder, run, engineReady, folderCount, fileCount, onError, onAsk, onSeeAll, onRunQuery, onEverywhere, onClear, preview, onPreview, voiceNote }: Props) {
  const [tab, setTab] = useState<TabId>("all");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [sort, setSort] = useState<Sort>({ col: "relevance", dir: "asc" });
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [route, setRoute] = useState<RouteReport | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [scopeNote, setScopeNote] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [slow, setSlow] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const [selected, setSelected] = useState<SearchResult[]>([]);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [weakOpen, setWeakOpen] = useState(false);
  const [showRoute, setShowRoute] = useState(false);
  const inflight = useRef<AbortController | null>(null);

  const tokens = filterTokens(tab, filters);
  const tokenKey = tokens.join(" ");
  const text = query.trim();

  // "Did you mean": what the backend would correct the draft to.
  useEffect(() => {
    if (!text || text.startsWith("?")) { setSuggestion(null); return; }
    const snap = voiceNote?.forQuery === text ? voiceNote.snap : null;
    let cancelled = false;
    suggest(text).then((res) => { if (!cancelled) setSuggestion(res.suggestion && res.suggestion !== text ? res.suggestion : snap); }).catch(() => { if (!cancelled) setSuggestion(snap); });
    return () => { cancelled = true; };
  }, [text, run, voiceNote]);

  const doSearch = useCallback(async () => {
    if (!text && !tokens.length) { setResults(null); setRoute(null); setFailure(null); return; }
    if (text.startsWith("?")) { const q = text.slice(1).trim(); if (q) onAsk(q); return; }
    if (!engineReady) return; // queued: runs by itself when the engine answers
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setSearching(true);
    setFailure(null);
    const t0 = performance.now();
    const slowTimer = window.setTimeout(() => setSlow(true), 150);
    const full = [text, ...tokens].join(" ").trim();
    try {
      let res;
      try {
        res = await search(full, "auto", 30, controller.signal, true, folder);
        setScopeNote(null);
      } catch (e) {
        // An engine that does not know or refuses the folder: say so, and search everywhere.
        if (folder && e instanceof ApiError && e.status >= 400 && e.status < 500) {
          res = await search(full, "auto", 30, controller.signal, true, null);
          setScopeNote(`Searching only in ${baseName(folder)} did not work${e.detail ? ` (${e.detail})` : ""}, so this search covers everywhere.`);
        } else throw e;
      }
      if (controller.signal.aborted) return;
      setResults(res.results);
      setRoute(res.route);
      setHint(res.hint ?? null);
      setLastQuery(text);
      setMs(performance.now() - t0);
      setWeakOpen(!res.results.some((r) => r.confidence !== "weak"));
    } catch (e) {
      const message = describeError(e);
      if (message) setFailure(message);
    } finally {
      window.clearTimeout(slowTimer);
      if (inflight.current === controller) { inflight.current = null; setSearching(false); setSlow(false); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, tokenKey, folder, engineReady, onAsk]);

  // Enter (a new `run`), another folder, a filter or the engine becoming ready: search.
  useEffect(() => { doSearch(); return () => inflight.current?.abort(); }, [doSearch, run]);

  const { strong, weak } = useMemo(() => {
    const all = results ?? [];
    const dir = sort.dir;
    const col = sort.col as SortCol;
    return { strong: sortGroup(all.filter((r) => r.confidence !== "weak"), col, dir), weak: sortGroup(all.filter((r) => r.confidence === "weak"), col, dir).slice(0, MAX_WEAK) };
  }, [results, sort]);
  const total = strong.length + weak.length;

  const counts = useMemo(() => {
    const c = { document: 0, image: 0, audio: 0 };
    (results ?? []).forEach((r) => { c[tabOf(r)]++; });
    return c;
  }, [results]);

  const items = useMemo<Item<SearchResult>[]>(() => {
    const out: Item<SearchResult>[] = [];
    if (strong.length && weak.length) out.push({ key: "g-strong", group: `Best matches (${strong.length})`, open: true, onToggle: () => undefined });
    strong.forEach((r) => out.push({ key: r.file_id, data: r }));
    if (weak.length) {
      out.push({ key: "g-weak", group: `Possible matches (${weak.length})`, open: weakOpen, onToggle: () => setWeakOpen((o) => !o) });
      if (weakOpen) weak.forEach((r) => out.push({ key: r.file_id, data: r }));
    }
    return out;
  }, [strong, weak, weakOpen]);

  const current = selected[0] ?? null;
  const lastRecorded = useRef<string | null>(null);
  const onSelection = useCallback((rows: SearchResult[]) => {
    setSelected(rows);
    if (rows.length === 1 && lastRecorded.current !== rows[0].file_id) { lastRecorded.current = rows[0].file_id; recordEvent("result_clicked", { file_id: rows[0].file_id, path: rows[0].path }); }
  }, []);

  const open = (r: SearchResult) => openResult(r.path, (m) => onError(m), r.file_id);
  const reveal = (r: SearchResult) => revealResult(r.path, (m) => onError(m), r.file_id);
  const copy = (rows: SearchResult[]) => { navigator.clipboard?.writeText(rows.map((r) => r.path ?? "").join("\n")).catch(() => onError("The path could not be copied.")); };

  const label = [text, ...chipLabels(filters)].join(" ");
  const countLine = results ? `${total} result${total === 1 ? "" : "s"}${ms !== null ? ` in ${Math.round(ms)} ms` : ""}` : "";
  useStatusBar({ left: searching && slow ? "Searching..." : failure ? "" : selected.length ? `${countLine}   ${selected.length} selected` : countLine, busy: searching && slow });

  const cols: Column<SearchResult>[] = [
    { id: "name", label: "Name and matching text", width: "minmax(220px,1fr)", sortable: true, render: (r) => {
      const { why, personal } = reasonOf(r);
      return (
        <span className="flex min-w-0 items-center gap-3">
          <FileIcon filename={r.filename} size={32} />
          <span className="min-w-0 flex-1 leading-4">
            <span className={`block truncate text-[14px] leading-5 ${r.confidence === "weak" ? "" : "font-semibold"}`} title={r.filename}>{r.filename}</span>
            {r.matched_chunk && <span className="block truncate text-[12px] text-sub"><Highlighted text={r.matched_chunk} /></span>}
            <span className="block truncate text-[12px] text-sub">{why}{personal && <span className="ml-3 text-[rgb(var(--c-amber-text))]">{personal}</span>}</span>
          </span>
        </span>
      );
    } },
    { id: "folder", label: "Folder", width: "minmax(110px,200px)", render: (r) => <span className="block truncate text-[12px] text-sub" style={{ direction: "rtl", textAlign: "left" }} title={r.path ?? ""}>{"‎" + (r.path ? r.path.replace(/[\\/][^\\/]*$/, "") : "")}</span> },
    { id: "modified", label: "Date modified", width: "130px", sortable: true, render: (r) => <span className="text-[12px] text-sub">{dateText(r.modified_time)}</span> },
    { id: "size", label: "Size", width: "70px", align: "right", sortable: true, render: (r) => <span className="text-[12px] text-sub">{sizeText(r.size)}</span> },
  ];
  const toggleSort = (col: string) => setSort((s) => (s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: col === "name" ? "asc" : "desc" }));

  const filterCount = filterChips(filters).length;
  const audioSearch = tab === "audio" || ["mp3", "m4a", "wav", "flac"].includes(filters.type);
  const folderName = folder ? baseName(folder) : "";
  const shownScopeFolder = folder && !scopeNote;

  const sortMenu = (["relevance", "name", "modified", "size"] as const).map((c) => ({
    id: c, label: { relevance: "Relevance", name: "Name", modified: "Date modified", size: "Size" }[c], checked: sort.col === c,
    onClick: () => setSort({ col: c, dir: c === "name" || c === "relevance" ? "asc" : "desc" }),
  }));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar>
        <CommandButton icon={G.sort} label="Sort" menu={{ items: sortMenu }} />
        <div className="relative">
          <button className="cmd text-[14px]" aria-haspopup="dialog" aria-expanded={filtersOpen} title="Filter" onClick={() => setFiltersOpen((o) => !o)}>
            <Fi g={G.filter} /><span className="max-[999px]:hidden">{filterCount ? `Filter (${filterCount})` : "Filter"}</span><Fi g={G.down} size={12} />
          </button>
          {filtersOpen && <FiltersPopover tab={tab} filters={filters} onChange={setFilters} onClose={() => setFiltersOpen(false)} />}
        </div>
        <CommandButton icon={G.more} label="More" menu={{ items: [{ id: "route", label: "How this was searched", checked: showRoute, onClick: () => setShowRoute((s) => !s) }] }} />
        <span className="flex-1" />
        <CommandButton icon={G.pane} label="Preview" pressed={preview} onClick={() => onPreview(!preview)} title="Preview pane (Alt+P)" />
      </CommandBar>

      {text || filterCount || tab !== "all" ? (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-rule px-4 py-2">
            <Pills label="Kind of file" value={tab} onChange={(t) => { setTab(t); if (filters.type && t !== "all" && !TYPES[t].includes(filters.type)) setFilters({ ...filters, type: "" }); }}
              items={TABS.map((t) => ({ id: t.id, label: t.label, count: t.id === "all" ? (results ? total : undefined) : results ? (tab === "all" ? counts[t.id] : tab === t.id ? total : undefined) : undefined }))} />
            {shownScopeFolder && (
              <span className="inline-flex h-8 items-center gap-1 rounded bg-[var(--pressed)] pl-3 pr-1 text-[14px]">
                In {folderName}
                <button className="grid h-6 w-6 place-items-center rounded hover:bg-[var(--hover)]" aria-label={`Search everywhere instead of only ${folderName}`} title="Search everywhere" onClick={onEverywhere}><Fi g={G.cancel} size={10} /></button>
              </span>
            )}
            {filterChips(filters).map((c) => (
              <span key={c.key} className="inline-flex h-6 items-center gap-1 rounded bg-[var(--pressed)] pl-2 pr-1 text-[12px]">
                {c.label}
                <button className="grid h-5 w-5 place-items-center rounded hover:bg-[var(--hover)]" aria-label={`Remove filter ${c.label}`} onClick={() => setFilters({ ...filters, [c.key]: "" })}><Fi g={G.cancel} size={10} /></button>
              </span>
            ))}
            {filterCount > 0 && <button className="text-[12px] text-accent hover:underline" onClick={() => setFilters(NO_FILTERS)}>Clear all</button>}
          </div>
          {voiceNote?.heard && voiceNote.forQuery === text && (
            <Banner tone="info" action={{ label: `Use "${voiceNote.heard}" instead`, onClick: () => onRunQuery(voiceNote.heard!) }}>Heard "{voiceNote.heard}", searching for {text}.</Banner>
          )}
          {suggestion && <Banner tone="info" action={{ label: `Search for "${suggestion}" instead`, onClick: () => onRunQuery(suggestion) }}>Did you mean <b className="font-semibold">{suggestion}</b>?</Banner>}
          {scopeNote && <Banner tone="warn">{scopeNote}</Banner>}
          {results && route?.suggest_ask && <Banner tone="info" action={{ label: "Ask instead", onClick: () => onAsk(lastQuery) }}>This reads like a question. Ask instead to get an answer from your files.</Banner>}
          {showRoute && route && <RouteLine route={route} count={total} />}
          {failure && <Banner tone="error" action={{ label: "Try again", onClick: () => doSearch() }}>The search didn't finish. {failure} Your words are kept.</Banner>}
        </>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className={`flex min-h-0 min-w-0 flex-1 flex-col transition-opacity ${searching && results ? "opacity-60" : ""}`} aria-busy={searching || undefined}>
          {!text && !filterCount && tab === "all" && <div className="min-h-0 flex-1 overflow-y-auto"><SearchHome onRun={onRunQuery} onSeeAll={onSeeAll} onError={(m) => onError(m)} /></div>}
          {(text || filterCount > 0 || tab !== "all") && !engineReady && !results && <EmptyState title="Waiting for the search engine..." text="Your search runs by itself as soon as it is ready." />}
          {(text || filterCount > 0 || tab !== "all") && engineReady && !results && !failure && !searching && !text && <EmptyState title="Type words to search" text="The filters are set. Add what the file says or is about." />}
          {results && total === 0 && !failure && (
            <NoResults text={lastQuery} folder={shownScopeFolder ? folderName : null} audio={audioSearch} hint={hint} route={route} fileCount={fileCount} folderCount={folderCount}
              filters={filterChips(filters)} onEverywhere={onEverywhere} onClear={onClear}
              onNames={() => { setTab("all"); setFilters({ ...filters, type: "" }); }} onClearFilters={() => { setFilters(NO_FILTERS); setTab("all"); }} />
          )}
          {total > 0 && (
            <>
              {strong.length === 0 && <p className="shrink-0 px-4 pt-3 text-[14px] text-sub"><b className="font-semibold text-ink">No best match.</b> The files below are only possible matches.</p>}
              <FileTable label={`Search results for ${label}`} columns={cols} items={items} rowHeight={56} sort={sort.col === "relevance" ? undefined : sort} onSort={toggleSort}
                getName={(r) => r.filename} onOpen={open} onReveal={reveal} onCopyPaths={copy} onSelection={onSelection} />
            </>
          )}
        </div>
        {preview && results && (
          <aside aria-label="Preview" className="w-[360px] shrink-0 border-l border-rule bg-content">
            <SearchPreview result={current} query={lastQuery} onError={(m) => onError(m)} />
          </aside>
        )}
      </div>
    </div>
  );
}

function filterChips(f: Filters): { key: keyof Filters; label: string }[] {
  const out: { key: keyof Filters; label: string }[] = [];
  if (f.type) out.push({ key: "type", label: f.type.toUpperCase() });
  if (f.after) out.push({ key: "after", label: `Modified after ${f.after}` });
  if (f.before) out.push({ key: "before", label: `Modified before ${f.before}` });
  if (f.size) out.push({ key: "size", label: SIZES.find((s) => s.id === f.size)?.label ?? "" });
  if (f.folder.trim()) out.push({ key: "folder", label: `Folder "${f.folder.trim()}"` });
  return out;
}
const chipLabels = (f: Filters) => filterChips(f).map((c) => c.label);

function RouteLine({ route, count }: { route: RouteReport; count: number }) {
  const detail = [route.reason, ...route.stages.filter((s) => !s.skipped && s.ms !== undefined).map((s) => `${s.stage}: ${s.ms} ms${s.hits !== undefined ? `, ${s.hits} hits` : ""}`)].join("\n");
  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-rule px-4 py-1.5 text-[12px] text-sub" title={detail}>
      <span>
        Searched by {TIER_LABEL[route.tier] ?? route.tier}
        {route.escalated && !route.tier.startsWith(route.requested_tier) && <> (tried {TIER_LABEL[route.requested_tier] ?? route.requested_tier} first, nothing confident)</>}, {route.total_ms.toFixed(0)} ms
      </span>
      {route.corrected_query && <span>Showing results for <b className="font-semibold text-ink">{route.corrected_query}</b></span>}
      <span className="ml-auto">{count} result{count === 1 ? "" : "s"}</span>
    </div>
  );
}

function NoResults({ text, folder, audio, hint, route, fileCount, folderCount, filters, onEverywhere, onClear, onNames, onClearFilters }: {
  text: string; folder: string | null; audio: boolean; hint: string | null; route: RouteReport | null; fileCount: number; folderCount: number;
  filters: { label: string }[]; onEverywhere: () => void; onClear: () => void; onNames: () => void; onClearFilters: () => void;
}) {
  const typed = text || "your search";
  const unrec = route?.unrecognized ?? [];
  // The words look like a file name ("budget.xlsx"): say why that file is not here.
  const [missing, setMissing] = useState<string | null>(null);
  useEffect(() => {
    setMissing(null);
    if (!/\.[a-z0-9]{2,5}$/i.test(text.trim())) return;
    let current = true;
    whyMissing(text.trim()).then((r) => { if (current && r.status !== "invalid") setMissing(r.reason); }).catch(() => { /* the search result already says nothing matched */ });
    return () => { current = false; };
  }, [text]);
  const spelling = unrec.length > 0 && <>Check the spelling of {unrec.map((w, i) => <span key={w}>{i > 0 && ", "}<i className="text-accent">"{w}"</i></span>)}: no file uses {unrec.length === 1 ? "that word" : "those words"}. </>;
  if (audio) {
    return (
      <EmptyState title={`No spoken words matched "${typed}".`}
        text={<>Audio search understands English speech only. Songs, music and other languages are usually not found. Try words that are spoken in English, or search the file name.{hint && !hint.startsWith("Speech search understands English only") && <> {hint}</>}</>}
        action={{ label: "Search file names instead", onClick: onNames }} secondary={{ label: "Clear search", onClick: onClear }} />
    );
  }
  return (
    <EmptyState
      title={folder ? `Nothing in ${folder} matches "${typed}".` : `Nothing on this computer matches "${typed}".`}
      text={<>{spelling}{folder ? "Checked file contents and names in this folder." : `Checked file contents, spoken audio and file names in ${folderCount} folder${folderCount === 1 ? "" : "s"} (${fileCount.toLocaleString()} files).`}{filters.length > 0 && ` Filters on: ${filters.map((f) => f.label).join(", ")}.`}{missing && <span className="mt-2 block text-ink">{missing}</span>}</>}
      action={folder ? { label: "Search everywhere", onClick: onEverywhere } : filters.length ? { label: "Clear filters", onClick: onClearFilters } : { label: "Clear search", onClick: onClear }}
      secondary={folder ? { label: "Clear search", onClick: onClear } : undefined} />
  );
}


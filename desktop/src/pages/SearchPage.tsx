import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { describeError, formatAgo, getRecommendations, listEvents, Recommendation, RouteReport, search, SearchResult, suggest } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER, MOD_KEY } from "../platform";
import { Omnibox } from "../ui/Omnibox";
import { FileIcon, PageHeader, shortPlace } from "../ui/kit";
import { Highlighted, openResult, revealResult } from "../ui/ResultCard";
import { SearchPreview } from "../ui/SearchPreview";
import { recordEvent } from "../backend";

// ---- tabs, filters and sort (docs/UI_DESIGN.md section 2) ----

type TabId = "all" | "document" | "image" | "audio";
const TABS: { id: TabId; label: string }[] = [
  { id: "all", label: "All" },
  { id: "document", label: "Documents" },
  { id: "image", label: "Images" },
  { id: "audio", label: "Audio" },
];
// File types offered in Filters > Type, per tab.
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

/** The filter words the backend understands, in the order they are shown. */
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

type SortId = "relevance" | "date" | "name";
function sortGroup(list: SearchResult[], by: SortId): SearchResult[] {
  if (by === "date") return [...list].sort((a, b) => b.modified_time - a.modified_time);
  if (by === "name") return [...list].sort((a, b) => a.filename.localeCompare(b.filename, undefined, { sensitivity: "base", numeric: true }));
  return list; // the backend's order
}

// "Searched by <tier>": the route the backend actually took.
const TIER_LABEL: Record<string, string> = {
  filename: "file name",
  metadata: "file details",
  keyword: "keywords",
  hybrid: "keywords + meaning",
  "hybrid+rerank": "keywords + meaning, then reranking",
};
const MAX_WEAK = 3;

function RouteLine({ route, count }: { route: RouteReport; count: number }) {
  const detail = [
    route.reason,
    ...route.stages.filter((s) => !s.skipped && s.ms !== undefined).map((s) => `${s.stage}: ${s.ms} ms${s.hits !== undefined ? `, ${s.hits} hits` : ""}`),
  ].join("\n");
  return (
    <div className="flex items-center gap-3 text-[12px] text-ink/65">
      <span title={detail}>
        Searched by {TIER_LABEL[route.tier] ?? route.tier}
        {route.escalated && !route.tier.startsWith(route.requested_tier) && <> (tried {TIER_LABEL[route.requested_tier] ?? route.requested_tier} first, nothing confident)</>}
        , <span className="mono">{route.total_ms.toFixed(0)} ms</span>
      </span>
      {route.corrected_query && <span>Showing results for <b className="font-medium text-ink/85">{route.corrected_query}</b></span>}
      <span className="ml-auto shrink-0 whitespace-nowrap">{count} result{count === 1 ? "" : "s"}</span>
    </div>
  );
}

function FiltersPopover({ tab, filters, onChange, onClose }: { tab: TabId; filters: Filters; onChange: (f: Filters) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    ref.current?.querySelector<HTMLElement>("select, input")?.focus();
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey, true); };
  }, [onClose]);
  const types = tab === "all" ? [...TYPES.document, ...TYPES.image, ...TYPES.audio] : TYPES[tab];
  const set = (k: keyof Filters) => (e: { currentTarget: { value: string } }) => onChange({ ...filters, [k]: e.currentTarget.value });
  const field = "mt-1 w-full rounded-md border border-rule-strong bg-content px-2 py-1.5 text-[13px] text-ink";
  return (
    <div ref={ref} role="dialog" aria-label="Filters" className="anim-pop absolute right-0 top-full z-20 mt-1 w-[300px] rounded-lg border border-rule-strong bg-content p-4 shadow-palette">
      <div className="grid grid-cols-2 gap-3 text-[12px] text-ink/70">
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
        <button className="btn-ghost px-2 py-1 text-[13px]" onClick={() => onChange(NO_FILTERS)}>Clear filters</button>
        <button className="btn-primary px-3 py-1 text-[13px]" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

// ---- the result list row ----

const PERSONAL_PREFIXES = ["You use this", "You used to", "Your usual", "Your most-used"];

function ResultRow({ r, index, selected, onSelect, onOpen }: { r: SearchResult; index: number; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  const weak = r.confidence === "weak";
  return (
    <div
      role="option"
      aria-selected={selected}
      data-index={index}
      className={`result-row flex cursor-default gap-3 border-b border-rule px-4 py-3 last:border-b-0 ${selected ? "bg-[color:var(--selected)] shadow-[inset_3px_0_0_var(--marker)]" : "hover:bg-ink/[0.03]"}`}
      onClick={onSelect}
      onDoubleClick={onOpen}
    >
      <FileIcon filename={r.filename} />
      <div className="min-w-0 flex-1">
        <div className={`truncate text-[14px] ${weak ? "font-medium text-ink/85" : "font-semibold"}`}>{r.filename}</div>
        {r.path && <div className="truncate text-[12px] text-ink/60" title={r.path}>{shortPlace(r.path)}</div>}
        {r.matched_chunk && <div className="mt-1 line-clamp-1 text-[13px] text-ink/80"><Highlighted text={r.matched_chunk} /></div>}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {r.why.slice(0, 2).map((w) => (
            <span key={w} className={`chip max-w-[260px] truncate ${PERSONAL_PREFIXES.some((p) => w.startsWith(p)) ? "chip-personal" : ""}`}>{w}</span>
          ))}
          <span className={`chip ${weak ? "" : "chip-accent"}`}>{weak ? "Weaker match" : "Strong match"}</span>
          <span className="ml-auto text-[12px] text-ink/55">Modified {formatAgo(r.modified_time)}</span>
        </div>
      </div>
    </div>
  );
}

/** Before any search: the searches you ran lately and files picked from how you work. */
function SearchHome({ onRun, onSeeAll, onError }: { onRun: (q: string) => void; onSeeAll: () => void; onError: (m: string) => void }) {
  const [recent, setRecent] = useState<string[] | null>(null);
  const [picks, setPicks] = useState<Recommendation[]>([]);
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
    }).catch(() => setRecent([]));
    getRecommendations().then((r) => {
      if (!r.enabled) return;
      const all = [...r.likely_next, ...r.usual_now, ...r.recent];
      setPicks(all.filter((x, i) => all.findIndex((y) => y.file_id === x.file_id) === i).slice(0, 4));
    }).catch(() => { /* nothing to show */ });
  }, []);
  return (
    <div className="space-y-6">
      {recent && recent.length > 0 && (
        <section>
          <h2 className="text-[15px] font-semibold">Recent searches</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {recent.map((q) => (
              <button key={q} className="btn-secondary max-w-[260px] truncate px-3 py-1.5 text-[13px]" onClick={() => onRun(q)}>{q}</button>
            ))}
          </div>
        </section>
      )}
      {picks.length > 0 && (
        <section>
          <div className="flex items-baseline justify-between">
            <h2 className="text-[15px] font-semibold">For You</h2>
            <button className="text-[13px] text-ink/65 hover:text-ink hover:underline" onClick={onSeeAll}>See all</button>
          </div>
          <ul className="mt-2 overflow-hidden rounded-lg border border-rule bg-content">
            {picks.map((r) => (
              <li key={r.file_id} className="border-b border-rule last:border-b-0">
                <button
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-ink/[0.03]"
                  onClick={() => { recordEvent("recommendation_clicked", { file_id: r.file_id, path: r.path }); openResult(r.path, onError, r.file_id); }}
                >
                  <FileIcon filename={r.filename} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium">{r.filename}</span>
                    <span className="block truncate text-[12px] text-ink/60">{shortPlace(r.path)}</span>
                  </span>
                  <span className="max-w-[45%] shrink-0 truncate text-right text-[12px] text-ink/60">{r.reason}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {recent !== null && (
        <section>
          <h2 className="text-[15px] font-semibold">Try a search</h2>
          <ul className="mt-2 overflow-hidden rounded-lg border border-rule bg-content">
            {EXAMPLES.map((ex) => (
              <li key={ex.q} className="border-b border-rule last:border-b-0">
                <button className="flex w-full items-baseline gap-3 px-4 py-2 text-left hover:bg-ink/[0.03]" onClick={() => onRun(ex.q)}>
                  <span className="mono text-[13px] text-ink">{ex.q}</span>
                  <span className="ml-auto shrink-0 text-[12px] text-ink/60">{ex.why}</span>
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

const EXAMPLES = [
  { q: "notes about the project deadline", why: "describe what it is about" },
  { q: "type:pdf after:2026-01-01 invoice", why: "narrow by type and date" },
  { q: "? when is the rent due", why: "ask a question, answered from your files" },
];

interface Props {
  active: boolean;
  folderCount: number;
  fileCount: number;
  onError: (m: string | null) => void;
  onAsk: (question: string) => void;
  onSeeAll: () => void;
}

export function SearchPage({ active, folderCount, fileCount, onError, onAsk, onSeeAll }: Props) {
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<TabId>("all");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [sort, setSort] = useState<SortId>("relevance");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [route, setRoute] = useState<RouteReport | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [slow, setSlow] = useState(false); // "Searching..." only after 150 ms
  const [failure, setFailure] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [heard, setHeard] = useState<string | null>(null);
  const voiceSnapRef = useRef<{ forQuery: string; snap: string } | null>(null);
  const inflightRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const tokens = filterTokens(tab, filters);
  const filterCount = tokens.length - (tab !== "all" && !filters.type ? 1 : 0);

  // "Did you mean": what the backend would correct the draft to, 250 ms after the last keystroke.
  useEffect(() => {
    const draft = query.trim();
    if (!draft || draft.startsWith("?")) { setSuggestion(null); return; }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const voiceSnap = voiceSnapRef.current?.forQuery === draft ? voiceSnapRef.current.snap : null;
      try {
        const res = await suggest(draft);
        if (!cancelled) setSuggestion(res.suggestion && res.suggestion !== draft ? res.suggestion : voiceSnap);
      } catch {
        if (!cancelled) setSuggestion(voiceSnap);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);

  const runSearch = useCallback(async (text: string, withTokens: string[]) => {
    text = text.trim();
    if (!text && !withTokens.length) return;
    if (text.startsWith("?")) { const q = text.slice(1).trim(); if (q) onAsk(q); return; }
    inflightRef.current?.abort();
    const controller = new AbortController();
    inflightRef.current = controller;
    setSearching(true);
    setFailure(null);
    const slowTimer = window.setTimeout(() => setSlow(true), 150);
    try {
      const res = await search([text, ...withTokens].join(" ").trim(), "auto", 20, controller.signal);
      if (controller.signal.aborted) return;
      setResults(res.results);
      setRoute(res.route);
      setLastQuery(text);
      setSelected(0);
    } catch (e) {
      const message = describeError(e);
      if (message) setFailure(message);
    } finally {
      window.clearTimeout(slowTimer);
      if (inflightRef.current === controller) { inflightRef.current = null; setSearching(false); setSlow(false); }
    }
  }, [onAsk]);

  // A tab or filter change re-runs the last search with the new filter words.
  const tokenKey = tokens.join(" ");
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    if (lastQuery || results) runSearch(lastQuery, tokens);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokenKey]);

  const voice = useVoice(
    useCallback((text: string, rawHeard?: string | null, snap?: string | null) => {
      setQuery(text);
      setHeard(rawHeard ?? null);
      voiceSnapRef.current = snap ? { forQuery: text.trim(), snap } : null;
      if (snap) setSuggestion(snap);
    }, []),
    useCallback((m: string) => onError(m), [onError]),
  );

  // One ranked list: strong first, then at most 3 weaker matches. Sort
  // reorders within each group and never moves a weak result above a strong one.
  const { strong, weak, ordered } = useMemo(() => {
    const all = results ?? [];
    const s = sortGroup(all.filter((r) => r.confidence !== "weak"), sort);
    const w = sortGroup(all.filter((r) => r.confidence === "weak"), sort).slice(0, MAX_WEAK);
    return { strong: s, weak: w, ordered: [...s, ...w] };
  }, [results, sort]);
  const current = ordered[selected] ?? null;

  const select = (i: number) => {
    setSelected(i);
    const r = ordered[i];
    if (r) recordEvent("result_clicked", { file_id: r.file_id, path: r.path });
  };
  const move = (dir: 1 | -1) => { if (ordered.length) setSelected((s) => Math.max(0, Math.min(ordered.length - 1, s + dir))); };
  const clear = () => { inflightRef.current?.abort(); setQuery(""); setResults(null); setRoute(null); setLastQuery(""); setFailure(null); setHeard(null); };

  // Enter in the box searches; pressed again on the same words it opens the selected file.
  const submit = () => {
    const text = query.trim();
    if (results && text === lastQuery && current) { openResult(current.path, onError, current.file_id); return; }
    runSearch(text, tokens);
  };

  useEffect(() => {
    if (!active) return;
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      const inField = target?.tagName === "INPUT" || target?.tagName === "SELECT" || target?.tagName === "TEXTAREA";
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && current) { e.preventDefault(); revealResult(current.path, onError, current.file_id); }
      else if (inField || filtersOpen) return;
      else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && current && target?.tagName !== "BUTTON") { e.preventDefault(); openResult(current.path, onError, current.file_id); }
      else if (e.key === "Escape") { e.preventDefault(); clear(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const total = ordered.length;

  return (
    <div className="flex h-full flex-col">
      {/* The search box, tabs, filters and the route line. */}
      <div className="shrink-0 border-b border-rule bg-canvas px-6 pt-5">
        {!results && !failure && <div className="mb-4"><PageHeader title="Search your files" subtitle="Find anything in the folders you indexed, by what it says or what it is about." /></div>}
        <Omnibox
          value={query}
          onChange={(v) => { setQuery(v); setHeard(null); }}
          onSubmit={submit}
          onEscape={clear}
          onArrow={move}
          placeholder="Search files, or ask a question with ?"
          searching={searching}
          voice={voice}
          autoFocus
        />
        {heard && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            Heard <span className="italic">"{heard}"</span>, searching for <span className="text-ink">{query}</span>
            <button className="btn-secondary px-2 py-0.5 text-[12px]" onClick={() => { setQuery(heard); setHeard(null); }}>Use "{heard}" instead</button>
          </div>
        )}
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            Did you mean
            <button className="btn-secondary px-2 py-0.5 text-[13px] font-medium text-accent" onClick={() => { setQuery(suggestion); setSuggestion(null); runSearch(suggestion, tokens); }}>{suggestion}</button>
          </div>
        )}

        <div className="mt-3 flex items-end gap-4">
          <div role="tablist" aria-label="Kind of file" className="flex gap-4">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                className={`-mb-px border-b-2 pb-2 text-[14px] ${tab === t.id ? "border-accent font-medium text-ink" : "border-transparent text-ink/65 hover:text-ink"}`}
                onClick={() => { setTab(t.id); if (filters.type && t.id !== "all" && !TYPES[t.id].includes(filters.type)) setFilters({ ...filters, type: "" }); }}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2 pb-1.5">
            <div className="relative">
              <button
                className={`flex items-center gap-1.5 px-2.5 py-1 text-[13px] ${filterCount ? "btn-secondary border-marker text-marker" : "btn-secondary"}`}
                aria-expanded={filtersOpen}
                onClick={() => setFiltersOpen((o) => !o)}
              >
                <span className="material-symbols-outlined icon-sm" aria-hidden>filter_list</span>
                Filters{filterCount ? ` (${filterCount})` : ""}
              </button>
              {filtersOpen && <FiltersPopover tab={tab} filters={filters} onChange={setFilters} onClose={() => setFiltersOpen(false)} />}
            </div>
            <label className="flex items-center gap-1.5 text-[13px] text-ink/70">
              Sort
              <select className="rounded-md border border-rule-strong bg-content px-2 py-1 text-[13px] text-ink" value={sort} onChange={(e) => setSort(e.currentTarget.value as SortId)}>
                <option value="relevance">Relevance</option>
                <option value="date">Date modified</option>
                <option value="name">Name</option>
              </select>
            </label>
          </div>
        </div>
        {tokens.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pb-2 text-[12px] text-ink/60">
            Filtered by {tokens.map((t) => <span key={t} className="mono chip">{t}</span>)}
          </div>
        )}
      </div>

      {/* Results and preview. */}
      <div className="flex min-h-0 flex-1">
        <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${results ? "border-r border-rule" : ""}`}>
          <div className="shrink-0 px-6 pb-2 pt-3" aria-live="polite">
            {slow ? <span className="text-[12px] text-ink/65">Searching...</span> : route && results && <RouteLine route={route} count={total} />}
          </div>

          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-6 pb-5">
            {failure && (
              <div role="alert" className="rounded-md border border-error/40 bg-error-soft px-3 py-2.5 text-[13px] text-error">
                {failure} Your search is kept: press Enter to try again.
              </div>
            )}

            {results && route?.suggest_ask && (
              <div className="mb-3 flex items-center gap-3 rounded-md border border-rule bg-content px-3 py-2 text-[13px] text-ink/80">
                This reads like a question. Ask instead to get an answer from your files.
                <button className="btn-primary ml-auto shrink-0 px-3 py-1 text-[13px]" onClick={() => onAsk(lastQuery)}>Ask instead</button>
              </div>
            )}

            {results && total === 0 && !failure && (
              <div className="py-10">
                <p className="text-[15px] font-semibold">Nothing on this computer matches "{[lastQuery, ...tokens].join(" ").trim()}".</p>
                <p className="mt-1 text-[13px] text-ink/70">
                  Checked file contents, spoken audio and file names in {folderCount} folder{folderCount === 1 ? "" : "s"} ({fileCount.toLocaleString()} files).
                  {tokens.length > 0 && " Removing a filter may help."}
                </p>
              </div>
            )}

            {total > 0 && (
              <div role="listbox" aria-label="Search results">
                {strong.length === 0 && (
                  <p className="mb-2 text-[13px] text-ink/75"><b className="font-medium text-ink">No confident match.</b> The files below are only loosely related.</p>
                )}
                {strong.length > 0 && (
                  <div className="overflow-hidden rounded-lg border border-rule bg-content">
                    {strong.map((r, i) => (
                      <ResultRow key={r.file_id} r={r} index={i} selected={selected === i} onSelect={() => select(i)} onOpen={() => openResult(r.path, onError, r.file_id)} />
                    ))}
                  </div>
                )}
                {weak.length > 0 && (
                  <>
                    <h2 className="mb-1.5 mt-4 text-[13px] font-medium text-ink/70">Weaker matches</h2>
                    <div className="overflow-hidden rounded-lg border border-rule bg-content">
                      {weak.map((r, j) => {
                        const i = strong.length + j;
                        return <ResultRow key={r.file_id} r={r} index={i} selected={selected === i} onSelect={() => select(i)} onOpen={() => openResult(r.path, onError, r.file_id)} />;
                      })}
                    </div>
                  </>
                )}
              </div>
            )}

            {!results && !searching && !failure && (
              <SearchHome onRun={(q) => { setQuery(q); runSearch(q, tokens); }} onSeeAll={onSeeAll} onError={onError} />
            )}
          </div>

          <div className="flex shrink-0 items-center gap-4 border-t border-rule px-5 py-2 text-[12px] text-ink/60">
            <span><span className="kbd">Up</span> <span className="kbd">Down</span> move</span>
            <span><span className="kbd">Enter</span> open</span>
            <span><span className="kbd">{MOD_KEY}+Enter</span> show in {FILE_MANAGER}</span>
            <span><span className="kbd">Esc</span> clear</span>
          </div>
        </div>

        {results && (
          <aside aria-label="Preview" className="w-[42%] min-w-[300px] max-w-[520px] shrink-0 bg-content">
            <SearchPreview result={current} query={lastQuery} onError={onError} />
          </aside>
        )}
      </div>
    </div>
  );
}

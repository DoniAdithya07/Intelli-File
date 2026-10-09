import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, engineUnreachable, listEvents, RouteReport, search, SearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER, MOD_KEY } from "../platform";
import { AskAnswer } from "../ui/AskAnswer";
import { FileIcon, LoadFailed, shortPlace } from "../ui/kit";
import { Omnibox } from "../ui/Omnibox";
import { openResult, revealResult } from "../ui/ResultCard";

const TIER_LABEL: Record<string, string> = {
  filename: "file name",
  metadata: "file details",
  keyword: "keywords",
  hybrid: "keywords + meaning",
  "hybrid+rerank": "keywords + meaning, then reranking",
};

interface Row { file_id: string | null; path: string; filename: string; confidence?: string }

interface Props {
  onError: (m: string | null) => void;
  onEscape: () => void;
}

/**
 * Ctrl+Space quick search (docs/UI_DESIGN.md section 15): the same search
 * as the main window in a small list. Before typing it lists the files you
 * opened lately; "?" asks a question.
 */
export function OverlaySearch({ onError, onEscape }: Props) {
  const [query, setQuery] = useState("");
  const [lastQuery, setLastQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [route, setRoute] = useState<RouteReport | null>(null);
  const [recent, setRecent] = useState<Row[]>([]);
  const [recentFailed, setRecentFailed] = useState(false); // the engine answered, with an error
  const [asking, setAsking] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [searching, setSearching] = useState(false);
  const inflight = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Recent files: the ones you opened, newest first, each once.
  useEffect(() => {
    listEvents(150).then((r) => {
      const seen = new Set<string>();
      const rows: Row[] = [];
      for (const e of r.events) {
        if ((e.kind === "file_opened" || e.kind === "recommendation_clicked") && e.path && !seen.has(e.path)) {
          seen.add(e.path);
          rows.push({ file_id: e.file_id, path: e.path, filename: e.path.split(/[\\/]/).pop() ?? e.path });
        }
        if (rows.length === 8) break;
      }
      setRecent(rows);
    }).catch((e) => { setRecent([]); setRecentFailed(!engineUnreachable(e)); });
  }, []);

  const run = useCallback(async (text: string) => {
    text = text.trim();
    if (!text) return;
    if (text.startsWith("?")) { const q = text.slice(1).trim(); if (q) { setResults(null); setAsking(q); } return; }
    setAsking(null);
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setSearching(true);
    onError(null);
    try {
      const res = await search(text, "auto", 12, controller.signal);
      if (controller.signal.aborted) return;
      // Strong matches first, then at most three weaker ones, as in the main window.
      const strong = res.results.filter((r) => r.confidence !== "weak");
      const weak = res.results.filter((r) => r.confidence === "weak").slice(0, 3);
      setResults([...strong, ...weak]);
      setRoute(res.route);
      setLastQuery(text);
      setSelected(0);
    } catch (e) {
      const message = describeError(e);
      if (message) onError(`${message} Your search is kept: press Enter to try again.`);
    } finally {
      if (inflight.current === controller) { inflight.current = null; setSearching(false); }
    }
  }, [onError]);

  const voice = useVoice(useCallback((t: string) => setQuery(t), []), useCallback((m: string) => onError(m), [onError]));

  const rows: Row[] = results
    ? results.filter((r) => r.path).map((r) => ({ file_id: r.file_id, path: r.path as string, filename: r.filename, confidence: r.confidence }))
    : query.trim() ? [] : recent;
  const current = rows[selected] ?? null;

  // Enter searches; pressed again on the same words it opens the selected file.
  const submit = () => {
    if (current && (results ? query.trim() === lastQuery : !query.trim())) { openResult(current.path, onError, current.file_id); return; }
    run(query);
  };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && current) { e.preventDefault(); revealResult(current.path, onError, current.file_id); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const move = (dir: 1 | -1) => { if (rows.length) setSelected((s) => Math.max(0, Math.min(rows.length - 1, s + dir))); };

  return (
    <div className="flex h-full flex-col">
      <Omnibox
        value={query}
        onChange={(v) => { setQuery(v); if (!v.trim()) { setResults(null); setAsking(null); setSelected(0); } }}
        onSubmit={submit}
        onEscape={onEscape}
        onArrow={move}
        listId={rows.length ? "overlay-results" : undefined}
        activeId={rows.length ? `overlay-result-${selected}` : undefined}
        placeholder="Search files, or ask with ?"
        searching={searching}
        voice={voice}
        autoFocus
        large
      />

      <div className="mt-2 min-h-0 flex-1 overflow-y-auto">
        {asking ? (
          <div className="pt-1"><AskAnswer key={asking} question={asking} onError={onError} /></div>
        ) : (
          <>
            <div className="px-1 pb-1 text-[12px] text-ink/65">
              {results
                ? route && `Searched by ${TIER_LABEL[route.tier] ?? route.tier}, ${route.total_ms.toFixed(0)} ms. ${results.length} result${results.length === 1 ? "" : "s"}`
                : !query.trim() && (recent.length ? "Recent files" : "Type to search your files. Start with ? to ask a question.")}
            </div>
            {!results && !query.trim() && recentFailed && <div className="px-1 pb-1"><LoadFailed what="your recent files" /></div>}
            {results && results.length === 0 && <p className="px-1 py-6 text-[14px] text-ink/70">Nothing on this computer matches "{lastQuery}".</p>}
            <ul ref={listRef} id="overlay-results" role="listbox" aria-label={results ? "Search results" : "Recent files"}>
              {rows.map((r, i) => (
                <li
                  key={(r.file_id ?? r.path) + i}
                  id={`overlay-result-${i}`}
                  role="option"
                  aria-selected={selected === i}
                  data-index={i}
                  className={`result-row flex cursor-default items-center gap-3 rounded-md px-2 py-1.5 ${selected === i ? "" : "hover:bg-ink/[0.04]"}`}
                  onClick={() => setSelected(i)}
                  onDoubleClick={() => openResult(r.path, onError, r.file_id)}
                >
                  <FileIcon filename={r.filename} size={32} />
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-[14px] ${r.confidence === "weak" ? "font-medium text-ink/80" : "font-semibold"}`}>{r.filename}</span>
                    <span className="block truncate text-[12px] text-ink/65" title={r.path}>{shortPlace(r.path)}</span>
                  </span>
                  {r.confidence && <span className={`shrink-0 text-[12px] ${r.confidence === "weak" ? "text-ink/65" : "text-marker"}`}>{r.confidence === "weak" ? "Weaker match" : "Strong match"}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="mt-2 flex shrink-0 items-center gap-4 border-t border-rule pt-2 text-[12px] text-ink/65">
        <span><span className="kbd">Enter</span> open</span>
        <span><span className="kbd">{MOD_KEY}+Enter</span> show in {FILE_MANAGER}</span>
        <span><span className="kbd">Esc</span> close</span>
      </div>
    </div>
  );
}

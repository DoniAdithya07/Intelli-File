import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, RouteReport, search, suggest, SearchMode, SearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER, MOD_KEY } from "../platform";
import { AskPanel } from "../ui/AskPanel";
import { Omnibox } from "../ui/Omnibox";
import { Recommendations } from "../ui/Recommendations";
import { openResult, ResultCard, revealResult } from "../ui/ResultCard";

const MODES: { id: SearchMode; label: string }[] = [
  { id: "auto", label: "Auto (routed)" },
  { id: "smart", label: "Smart (Semantic AI)" },
  { id: "exact", label: "Exact Match" },
  { id: "keyword", label: "Keyword" },
];

const TIER_LABEL: Record<string, string> = {
  filename: "filename",
  metadata: "metadata",
  keyword: "keyword",
  hybrid: "hybrid",
  "hybrid+rerank": "hybrid + rerank",
};

/** Phase 18: the route badge — which tier answered, what it skipped, what each stage cost. */
function RouteBadge({ route }: { route: RouteReport }) {
  const ran = route.stages.filter((s) => !s.skipped && s.ms !== undefined);
  const skipped = route.stages.filter((s) => s.skipped).map((s) => s.stage);
  const title = [
    `${route.reason}`,
    route.escalated ? `Escalated from ${route.requested_tier} to ${route.tier} (the cheap route found nothing confident).` : "",
    ...ran.map((s) => `${s.stage}: ${s.ms} ms${s.hits !== undefined ? ` (${s.hits} hits)` : ""}${s.relevant !== undefined ? ` (${s.relevant}/${s.candidates} judged relevant)` : ""}`),
    skipped.length ? `skipped: ${skipped.join(", ")}` : "",
  ].filter(Boolean).join("\n");
  return (
    <span className="chip inline-flex items-center gap-1.5" title={title}>
      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>alt_route</span>
      {TIER_LABEL[route.tier] ?? route.tier}
      {route.escalated && <span className="text-accent">↑</span>}
      <span className="text-white/40">·</span>
      {route.total_ms.toFixed(0)} ms
      {skipped.length > 0 && <span className="text-white/35">· skipped {skipped.length}</span>}
    </span>
  );
}

interface Props {
  folderCount: number;
  fileCount: number;
  onError: (m: string | null) => void;
  compact?: boolean; // overlay
  onEscape?: () => void;
}

export function SearchPage({ folderCount, fileCount, onError, compact, onEscape }: Props) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("auto");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [route, setRoute] = useState<RouteReport | null>(null);
  // Phase 19: Ask mode — the question the agent is working on (null = normal search).
  const [asking, setAsking] = useState<string | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [selected, setSelected] = useState(0);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  // A sound-alike file name from voice search, kept for the transcript it came with
  // so the spelling-suggestion effect below doesn't clear it (2026-09-20).
  const voiceSnapRef = useRef<{ forQuery: string; snap: string } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // The search in flight; a newer search aborts it so a slow old reply can
  // never overwrite a newer one (2026-09-21 macOS pass — the previous guard
  // silently ignored the second Enter instead).
  const inflightRef = useRef<AbortController | null>(null);

  // "Did you mean": ask the backend what it would correct the draft to, 250 ms
  // after the last keystroke. A stale reply for an older draft is discarded.
  useEffect(() => {
    const draft = query.trim();
    if (!draft) { setSuggestion(null); return; }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const res = await suggest(draft);
        const voiceSnap = voiceSnapRef.current?.forQuery === draft ? voiceSnapRef.current.snap : null;
        if (!cancelled) setSuggestion(res.suggestion && res.suggestion !== draft ? res.suggestion : voiceSnap);
      } catch {
        if (!cancelled) setSuggestion(voiceSnapRef.current?.forQuery === draft ? voiceSnapRef.current.snap : null);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);

  const runSearch = useCallback(async (q?: string, m?: SearchMode) => {
    const text = (q ?? query).trim();
    const useMode = m ?? mode;
    if (!text) return;
    // "? what did the landlord say" — a leading question mark hands the query to the agent.
    if (text.startsWith("?")) {
      setResults(null);
      setAsking(text.slice(1).trim() || null);
      return;
    }
    setAsking(null);
    inflightRef.current?.abort();
    const controller = new AbortController();
    inflightRef.current = controller;
    setSearching(true);
    onError(null);
    const t0 = performance.now();
    try {
      const res = await search(text, useMode, 20, controller.signal);
      if (controller.signal.aborted) return;
      setResults(res.results);
      setRoute(res.route);
      setLastQuery(text);
      setElapsed(performance.now() - t0);
      setSelected(0);
    } catch (e) {
      const message = describeError(e);
      if (message) onError(message);
    } finally {
      if (inflightRef.current === controller) { inflightRef.current = null; setSearching(false); }
    }
  }, [query, mode, onError]);

  // When the backend snapped a misheard file name ("Learn Lord letter" →
  // "landlord letter") the raw words are kept so the user can see it and
  // switch back with one click. Cleared as soon as the query changes.
  const [heard, setHeard] = useState<string | null>(null);
  const voice = useVoice(
    useCallback((text: string, rawHeard?: string | null, snap?: string | null) => {
      setQuery(text); // lands in the box; the user confirms with Enter
      setHeard(rawHeard ?? null);
      voiceSnapRef.current = snap ? { forQuery: text.trim(), snap } : null; // sound-alike file name, offered via the same chip as spelling
      if (snap) setSuggestion(snap);
    }, []),
    useCallback((m: string) => onError(m), [onError]),
  );

  const strong = (results ?? []).filter((r) => r.confidence !== "weak");
  const weak = (results ?? []).filter((r) => r.confidence === "weak");
  const ordered = [...strong, ...weak];

  function move(dir: 1 | -1) {
    if (!ordered.length) return;
    setSelected((s) => Math.max(0, Math.min(ordered.length - 1, s + dir)));
  }

  // Keyboard: ↑↓ navigate, ↵ open, ⌘/Ctrl+↵ reveal — global to the page so it works
  // whether the input or a card has focus.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const inInput = (e.target as HTMLElement)?.tagName === "INPUT";
      if (e.key === "ArrowDown" && !inInput) { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp" && !inInput) { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && ordered[selected] && (e.metaKey || e.ctrlKey)) { e.preventDefault(); revealResult(ordered[selected].path, onError, ordered[selected].file_id); }
      else if (e.key === "Enter" && !inInput && ordered[selected]) { e.preventDefault(); openResult(ordered[selected].path, onError, ordered[selected].file_id); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const total = results?.length ?? 0;

  return (
    <div className="flex h-full flex-col gap-3">
      <div className={compact ? "" : "panel p-3"}>
        <Omnibox
          value={query}
          onChange={(v) => { setQuery(v); setHeard(null); if (!v.trim()) setAsking(null); }}
          onSubmit={() => runSearch()}
          onEscape={onEscape}
          onArrow={move}
          placeholder="Describe what you're looking for… (“exact phrase” or type:pdf also work)"
          searching={searching}
          voice={voice}
          autoFocus
          large={compact}
        />
        {heard && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-white/60">
            <span className="material-symbols-outlined icon-sm text-accent">hearing</span>
            Heard <span className="italic text-white/45">“{heard}”</span> — searching for <span className="text-white/85">{query}</span>
            <button
              className="chip hover:brightness-125"
              onClick={() => { setQuery(heard); setHeard(null); }}
              title="Use exactly what was heard instead"
            >
              use “{heard}”
            </button>
          </div>
        )}
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-white/60">
            <span className="material-symbols-outlined icon-sm text-accent">spellcheck</span>
            Did you mean:
            <button
              className="chip chip-accent hover:brightness-125"
              onClick={() => { setQuery(suggestion); setSuggestion(null); runSearch(suggestion); }}
              title="Replace the query with this spelling and search"
            >
              {suggestion}
            </button>
          </div>
        )}
        <div className="mt-3 flex items-center justify-between">
          <div className="flex gap-1 rounded-lg bg-white/[0.03] p-1">
            {MODES.map((m) => (
              <button
                key={m.id}
                className={`mono rounded-md px-3 py-1 text-[11px] transition-colors ${mode === m.id ? "bg-accent/15 text-accent" : "text-white/55 hover:text-white"}`}
                onClick={() => { setMode(m.id); if (lastQuery) runSearch(lastQuery, m.id); }}
              >
                {mode === m.id && <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" />}
                {m.label}
              </button>
            ))}
          </div>
          {!compact && (
            <span className="mono flex items-center gap-2 text-[11px] text-white/40">
              <button
                className={`mono rounded-md px-3 py-1 text-[11px] transition-colors ${asking ? "bg-accent/15 text-accent" : "text-white/55 hover:text-white"}`}
                title="Ask the local AI a question about your files (or start the query with ?)"
                onClick={() => { const t = query.trim().replace(/^\?/, "").trim(); if (t) { setResults(null); setAsking(t); } }}
              >
                <span className="material-symbols-outlined mr-1 align-middle" style={{ fontSize: 13 }}>smart_toy</span>Ask
              </button>
              {route && <RouteBadge route={route} />}
              {elapsed !== null && <span>Search: {elapsed.toFixed(0)} ms</span>}
            </span>
          )}
        </div>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto pr-1">
        {asking && <AskPanel question={asking} onError={onError} compact={compact} />}

        {results && route?.suggest_ask && !asking && (
          <div className="mb-3 flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-[13px] text-white/70">
            <span className="material-symbols-outlined icon-sm text-accent">smart_toy</span>
            Looks like a question the index couldn't answer confidently.
            <button className="chip chip-accent hover:brightness-125" onClick={() => { setResults(null); setAsking(lastQuery); }}>Ask IntelliFile</button>
          </div>
        )}

        {searching && !results && (
          <div className="space-y-2">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-20" style={{ width: `${100 - i * 8}%` }} />)}</div>
        )}

        {results && total === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-white/30" style={{ fontSize: 40 }}>folder_off</span>
            <p className="text-[15px] font-semibold">Nothing on this computer matches “{lastQuery}”.</p>
            <p className="mt-1 text-[13px] text-white/50">Checked file contents, spoken audio and file names in {folderCount} folder{folderCount === 1 ? "" : "s"} · {fileCount.toLocaleString()} files.</p>
          </div>
        )}

        {results && total > 0 && (
          <>
            {strong.length > 0 ? (
              <div className="mono mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wider text-white/50">
                <span className="material-symbols-outlined icon-sm text-accent">auto_awesome</span>
                Top matches (high confidence)
                <span className="ml-auto normal-case tracking-normal text-white/35">Ranked by fusion of keyword + meaning</span>
              </div>
            ) : (
              <p className="mb-3 text-[13px] text-white/60">
                <b className="text-white/85">No confident match for “{lastQuery}”.</b> The files below are only loosely related — it's probably not in the indexed folders.
              </p>
            )}
            <div className="rise space-y-2">
              {strong.map((r, i) => (
                <div key={r.file_id} data-index={i}>
                  <ResultCard result={r} selected={selected === i} compact={compact} onSelect={() => setSelected(i)} onError={onError} />
                </div>
              ))}
            </div>
            {weak.length > 0 && (
              <>
                <div className="mono my-3 flex items-center gap-3 text-[11px] uppercase tracking-wider text-white/40">
                  Possibly related (weaker matches)<span className="h-px flex-1 bg-white/[0.07]" />
                </div>
                <div className="rise space-y-2">
                  {weak.map((r, j) => {
                    const i = strong.length + j;
                    return (
                      <div key={r.file_id} data-index={i}>
                        <ResultCard result={r} selected={selected === i} compact={compact} onSelect={() => setSelected(i)} onError={onError} />
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </>
        )}

        {!results && !searching && !asking && <Recommendations onError={onError} compact={compact} />}

        {!results && !searching && !asking && !compact && (
          <div className="flex flex-col items-center justify-center py-10 text-center text-white/45">
            <span className="material-symbols-outlined mb-3 text-white/25" style={{ fontSize: 40 }}>manage_search</span>
            <p className="text-[14px]">Type what you remember about the file — its topic, a phrase, what was said in a recording.</p>
            <p className="mt-1 text-[12px]">Try: <i>notes about traffic spikes</i> · <i>what time does my train leave</i> · <i>abhisek plan</i></p>
            <p className="mt-3 flex items-center gap-1.5 text-[12px]">
              <span className="material-symbols-outlined icon-sm text-accent">mic</span>
              Speaking? Your file names are recognised even as one word — for anything else, a short phrase like <i className="text-white/70">"find my gym schedule"</i> is heard more reliably than <i className="text-white/70">"gym"</i> alone.
            </p>
          </div>
        )}
      </div>

      <div className="mono flex items-center gap-4 text-[11px] text-white/45">
        <span><span className="kbd">↑↓</span> navigate</span>
        <span><span className="kbd">↵</span> open</span>
        <span><span className="kbd">{MOD_KEY}↵</span> reveal in {FILE_MANAGER}</span>
        <span><span className="kbd">esc</span> clear</span>
        {results && <span className="ml-auto text-accent"><span className="material-symbols-outlined icon-sm">bolt</span> {total} result{total === 1 ? "" : "s"} from {fileCount.toLocaleString()} indexed files</span>}
      </div>
    </div>
  );
}

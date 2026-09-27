import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, RouteReport, search, suggest, SearchMode, SearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER, MOD_KEY } from "../platform";
import { AskPanel } from "../ui/AskPanel";
import { Omnibox } from "../ui/Omnibox";
import { Recommendations } from "../ui/Recommendations";
import { openResult, RESULT_LIST, ResultCard, revealResult } from "../ui/ResultCard";

const MODES: { id: SearchMode; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "IntelliFile picks the cheapest search that answers the query" },
  { id: "smart", label: "Meaning", hint: "Keywords and meaning together, every time" },
  { id: "exact", label: "Exact phrase", hint: "The whole query as one phrase" },
  { id: "keyword", label: "Keywords", hint: "Word matches only" },
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
    <span className="inline-flex items-center gap-1.5 text-[12px] text-ink/70" title={title}>
      <span className="material-symbols-outlined text-ink/60" style={{ fontSize: 14 }}>alt_route</span>
      <span>
        Searched by {TIER_LABEL[route.tier] ?? route.tier}{route.escalated && " after a first try"}, <span className="mono">{route.total_ms.toFixed(0)} ms</span>
        {skipped.length > 0 && <>, {skipped.length} step{skipped.length === 1 ? "" : "s"} skipped</>}
      </span>
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

export function OverlaySearch({ folderCount, fileCount, onError, compact, onEscape }: Props) {
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
          placeholder="Describe the file you want, or ask a question starting with ?"
          searching={searching}
          voice={voice}
          autoFocus
          large={compact}
        />
        {heard && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            <span className="material-symbols-outlined icon-sm text-accent">hearing</span>
            Heard <span className="italic text-ink/70">“{heard}”</span>, searching for <span className="text-ink/90">{query}</span>
            <button
              className="btn-secondary px-2 py-0.5 text-[12px]"
              onClick={() => { setQuery(heard); setHeard(null); }}
              title="Use exactly what was heard instead"
            >
              Use “{heard}” instead
            </button>
          </div>
        )}
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            <span className="material-symbols-outlined icon-sm text-accent">spellcheck</span>
            Did you mean
            <button
              className="btn-secondary px-2 py-0.5 text-[13px] font-medium text-accent"
              onClick={() => { setQuery(suggestion); setSuggestion(null); runSearch(suggestion); }}
              title="Replace the query with this spelling and search"
            >
              {suggestion}
            </button>
          </div>
        )}
        <div className="mt-3 flex items-center justify-between gap-3">
          <div role="radiogroup" aria-label="Search mode" className="flex rounded-md border border-rule-strong">
            {MODES.map((m, i) => (
              <button
                key={m.id}
                role="radio"
                aria-checked={mode === m.id}
                title={m.hint}
                className={`px-3 py-1 text-[13px] transition-colors ${i > 0 ? "border-l border-rule-strong" : ""} ${
                  mode === m.id ? "bg-accent text-on-accent" : "text-ink/80 hover:bg-ink/[0.04]"
                } ${i === 0 ? "rounded-l-[5px]" : ""} ${i === MODES.length - 1 ? "rounded-r-[5px]" : ""}`}
                onClick={() => { setMode(m.id); if (lastQuery) runSearch(lastQuery, m.id); }}
              >
                {m.label}
              </button>
            ))}
          </div>
          {!compact && (
            <span className="flex items-center gap-3 text-[12px] text-ink/70">
              {route && <RouteBadge route={route} />}
              <button
                className={`px-3 py-1 text-[13px] ${asking ? "btn-primary" : "btn-secondary"}`}
                title="Ask a question about your files. The answer is written on this computer and cites the files it used."
                onClick={() => { const t = query.trim().replace(/^\?/, "").trim(); if (t) { setResults(null); setAsking(t); } }}
              >
                Ask a question
              </button>
            </span>
          )}
        </div>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto pr-1">
        {asking && <AskPanel question={asking} onError={onError} compact={compact} />}

        {results && route?.suggest_ask && !asking && (
          <div className="mb-3 flex items-center gap-2 rounded-md border border-rule bg-content px-3 py-2 text-[13px] text-ink/80">
            <span className="material-symbols-outlined icon-sm text-accent">help</span>
            This reads like a question, and no file answers it clearly.
            <button className="btn-primary ml-auto px-3 py-1 text-[13px]" onClick={() => { setResults(null); setAsking(lastQuery); }}>Ask instead</button>
          </div>
        )}

        {searching && !results && (
          <div className="space-y-2">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-16" style={{ width: `${100 - i * 8}%` }} />)}</div>
        )}

        {results && total === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-ink/55" style={{ fontSize: 40 }}>folder_off</span>
            <p className="text-[15px] font-semibold">Nothing on this computer matches “{lastQuery}”.</p>
            <p className="mt-1 text-[13px] text-ink/70">Checked file contents, spoken audio and file names in {folderCount} folder{folderCount === 1 ? "" : "s"} ({fileCount.toLocaleString()} files).</p>
          </div>
        )}

        {results && total > 0 && (
          <>
            {strong.length > 0 ? (
              <div className="mb-2 flex items-baseline gap-2 text-[13px]">
                <span className="font-semibold">Best matches</span>
                <span className="text-ink/60">ranked by keywords and meaning together</span>
              </div>
            ) : (
              <p className="mb-3 text-[13px] text-ink/70">
                <b className="text-ink/90">No confident match for “{lastQuery}”.</b> The files below are only loosely related. It is probably not in the folders you indexed.
              </p>
            )}
            {strong.length > 0 && (
              <div className={RESULT_LIST}>
                {strong.map((r, i) => (
                  <div key={r.file_id} data-index={i}>
                    <ResultCard result={r} selected={selected === i} compact={compact} onSelect={() => setSelected(i)} onError={onError} />
                  </div>
                ))}
              </div>
            )}
            {weak.length > 0 && (
              <>
                <div className="mb-2 mt-4 text-[13px] text-ink/70">Possibly related, weaker matches</div>
                <div className={RESULT_LIST}>
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
          <div className="mx-auto max-w-[62ch] py-10 text-ink/70">
            <p className="text-[15px] font-semibold text-ink">Search by what the file is about</p>
            <p className="mt-1 text-[14px]">Type what you remember: its topic, a phrase from it, or what was said in a recording. File names work too, even misspelled.</p>
            <ul className="mt-3 space-y-1 text-[13px]">
              <li><i>notes about traffic spikes</i></li>
              <li><i>type:pdf lecture 7</i></li>
              <li><i>? when is the march invoice due</i> asks a question and answers it from your files</li>
            </ul>
            <p className="mt-3 flex items-start gap-1.5 text-[13px]">
              <span className="material-symbols-outlined icon-sm text-accent">mic</span>
              <span>Speaking works best with a short phrase such as <i>find my gym schedule</i>. Your file names are recognised even as a single word.</span>
            </p>
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 text-[12px] text-ink/60">
        <span><span className="kbd">Up</span> <span className="kbd">Down</span> move</span>
        <span><span className="kbd">Enter</span> open</span>
        <span><span className="kbd">{MOD_KEY}+Enter</span> show in {FILE_MANAGER}</span>
        <span><span className="kbd">Esc</span> clear</span>
        {results && <span className="ml-auto">{total} result{total === 1 ? "" : "s"} from {fileCount.toLocaleString()} indexed files{elapsed !== null && <>, <span className="mono">{elapsed.toFixed(0)} ms</span></>}</span>}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, formatTimestamp, search, searchVisual, suggestVisual, VisualKind, VisualSearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER } from "../platform";
import { Thumb } from "../ui/Thumb";
import { PageHeader, Tabs } from "../ui/kit";
import { Omnibox } from "../ui/Omnibox";
import { openResult, revealResult } from "../ui/ResultCard";

interface Props {
  photoCount: number;
  videoCount: number;
  available: boolean; // false when the photo and video model did not load
  onError: (m: string | null) => void;
}

const KINDS: { id: VisualKind; label: string }[] = [
  { id: "all", label: "All" },
  { id: "photo", label: "Photos" },
  { id: "video", label: "Videos" },
];

function PhotoCard({ r, strong, onError, browsing }: { r: VisualSearchResult; strong: boolean; onError: (m: string) => void; browsing?: boolean }) {
  const [broken, setBroken] = useState(false);
  return (
    <div
      tabIndex={0}
      aria-label={`${r.filename}${browsing ? "" : strong ? ", strong match" : ", weaker match"}. Enter opens it.`}
      className={`panel group cursor-default overflow-hidden ${strong || browsing ? "" : "opacity-70"}`}
      onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) openResult(r.path, onError, r.file_id); }}
      onDoubleClick={() => openResult(r.path, onError, r.file_id)}
      title={`Double-click to open. Right-click to show in ${FILE_MANAGER}.`}
      onContextMenu={(e) => { e.preventDefault(); revealResult(r.path, onError, r.file_id); }}
    >
      <div className="relative aspect-[4/3] bg-ink/[0.03]">
        {r.path && !broken && <Thumb path={r.path} size={320} at={r.timestamp_offset_seconds} alt={r.filename} className="h-full w-full object-cover" onError={() => setBroken(true)} />}
        {broken && <div className="grid h-full place-items-center px-3 text-center text-[12px] text-ink/65">No preview for {r.filename}</div>}
        {!browsing && (
          <span className="absolute left-2 top-2 rounded bg-black/70 px-2 py-0.5 text-[12px] text-white">{strong ? "Strong match" : "Weaker match"}</span>
        )}
        {r.kind === "video" && r.timestamp_offset_seconds != null && (
          <span className="mono absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-0.5 text-[12px] text-white" title="The moment that matched. Open the video and go to this time.">
            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>play_arrow</span>
            {formatTimestamp(r.timestamp_offset_seconds)}
          </span>
        )}
        <div className="absolute inset-x-0 bottom-0 flex justify-end gap-1 p-2 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button className="rounded bg-black px-1.5 py-0.5 text-[12px] text-white hover:bg-black/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white" onClick={() => openResult(r.path, onError, r.file_id)}>Open</button>
          <button className="rounded bg-black px-1.5 py-0.5 text-[12px] text-white hover:bg-black/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white" onClick={() => revealResult(r.path, onError, r.file_id)}>Show in {FILE_MANAGER}</button>
        </div>
      </div>
      {r.kind === "video" && r.path && r.moments && r.moments.length > 1 && (
        <div className="flex gap-1 px-2 pt-2" title="The moments that matched">
          {r.moments.map((m) => (
            <div key={m.t} className="relative min-w-0 flex-1 overflow-hidden rounded-md bg-black/40">
              <Thumb path={r.path!} size={160} at={m.t} alt="" className="aspect-video w-full object-cover" />
              <span className="mono absolute bottom-0.5 right-1 rounded bg-black/70 px-1 text-[11px] text-white">{formatTimestamp(m.t)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="px-3 py-2">
        <div className="truncate text-[13px] font-medium">{r.filename}</div>
        <div className="mono mt-0.5 flex justify-between text-[12px] text-ink/65">
          <span>{r.captured_at ? new Date(r.captured_at).toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" }) : "No date"}</span>
          <span>{r.kind ?? "photo"}</span>
        </div>
      </div>
    </div>
  );
}

export function PhotosPage({ photoCount, videoCount, available, onError }: Props) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<VisualKind>("all");
  const [results, setResults] = useState<VisualSearchResult[] | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [unrecognized, setUnrecognized] = useState<string[]>([]);
  const [suggestion, setSuggestion] = useState<string | null>(null);

  // "Did you mean" — same debounce as the Search page, against the photo-query checker.
  useEffect(() => {
    const draft = query.trim();
    if (!draft) { setSuggestion(null); return; }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const res = await suggestVisual(draft);
        if (!cancelled) setSuggestion(res.suggestion && res.suggestion !== draft ? res.suggestion : null);
      } catch {
        if (!cancelled) setSuggestion(null);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);

  // A newer search (another query, or another tab) cancels the one in flight; its reply is ignored.
  const inflight = useRef<AbortController | null>(null);
  const run = useCallback(async (q?: string, k?: VisualKind) => {
    const text = (q ?? query).trim();
    if (!text) return;
    inflight.current?.abort();
    const mine = new AbortController();
    inflight.current = mine;
    setSearching(true);
    onError(null);
    const t0 = performance.now();
    try {
      const res = await searchVisual(text, k ?? kind, 24, mine.signal);
      if (mine.signal.aborted) return;
      if (res.error) { onError(res.error); setResults(null); }
      else {
        setResults(res.results ?? []);
        setUnrecognized(res.unrecognized ?? []);
        setLastQuery(res.corrected_query ?? text);
        setElapsed(performance.now() - t0);
      }
    } catch (e) {
      if (!mine.signal.aborted) onError(describeError(e) || null);
    } finally {
      if (inflight.current === mine) { inflight.current = null; setSearching(false); }
    }
  }, [query, kind, onError]);

  // Another tab: the results on screen belong to the old kind, so they go at once, and the
  // words in the box (not the last query) are searched again for the new kind, also when
  // the first search has not answered yet.
  const switchKind = (k: VisualKind) => {
    setKind(k);
    if (results === null && !searching) return;
    setResults(null);
    if (query.trim()) run(query, k);
    else { inflight.current?.abort(); inflight.current = null; setSearching(false); }
  };

  const voice = useVoice(useCallback((t: string) => setQuery(t), []), useCallback((m: string) => onError(m), [onError]));

  // Before a search: the newest photos and videos in the index, as they are.
  const [latest, setLatest] = useState<VisualSearchResult[] | null>(null);
  const [latestFailed, setLatestFailed] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const words = kind === "photo" ? ["type:image"] : kind === "video" ? ["type:video"] : ["type:image", "type:video"];
    Promise.all(words.map((w) => search(w, "auto", 40, undefined, false)))
      .then((all) => {
        if (cancelled) return;
        const files = all.flatMap((r) => r.results).sort((a, b) => b.modified_time - a.modified_time).slice(0, 40);
        setLatestFailed(null);
        setLatest(files.map((f) => ({
          file_id: f.file_id, path: f.path, filename: f.filename, score: 0, confidence: "strong",
          kind: /\.(mp4|mov|m4v|mkv|webm|avi)$/i.test(f.filename) ? "video" : "photo",
          captured_at: new Date(f.modified_time * 1000).toISOString(), timestamp_offset_seconds: null, moments: null,
        })));
      })
      .catch((e) => { if (!cancelled) setLatestFailed(describeError(e) || "The search engine did not answer."); });
    return () => { cancelled = true; };
  }, [kind, reloadKey]);
  // Belt and braces: a tab never shows the other kind, whatever the reply held.
  const shown = kind === "all" ? results ?? [] : (results ?? []).filter((r) => (r.kind ?? "photo") === kind);
  const strong = shown.filter((r) => r.confidence !== "weak");
  const weak = shown.filter((r) => r.confidence === "weak");

  return (
    <div className="flex h-full flex-col gap-3">
      <PageHeader title="Photos and videos" subtitle="Find pictures and video moments by describing what is in them." />
      {!available && (
        <div role="alert" className="rounded-md border border-rule-strong bg-content px-4 py-3 text-[13px]">
          <b>Searching by what is in a picture is not available.</b> The photo and video model did not load. Extract IntelliFile-windows.zip again, completely, then reopen IntelliFile. Photos can still be found by file name on the Search page.
        </div>
      )}
      <div>
        <Omnibox value={query} onChange={(v) => { setQuery(v); if (!v.trim()) setResults(null); }} onSubmit={() => run()} placeholder="Describe a photo" icon="image_search" searching={searching} voice={voice} autoFocus />
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            Did you mean
            <button className="btn-secondary px-2 py-0.5 text-[13px] font-medium text-accent" onClick={() => { setQuery(suggestion); setSuggestion(null); run(suggestion); }}>{suggestion}</button>
          </div>
        )}
        <div className="mt-3 flex items-end justify-between gap-3 border-b border-rule">
          <Tabs label="Photo or video" tabs={KINDS} value={kind} onChange={switchKind} />
          <span className="pb-2 text-[12px] text-ink/65">A short phrase works best: <i>a person in a red shirt</i></span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1" aria-busy={searching || (!results && !latest && !latestFailed) || undefined}>
        {searching && !results && <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton aspect-[4/3]" />)}</div>}

        {results && shown.length === 0 && unrecognized.length > 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-ink/65" style={{ fontSize: 40 }}>spellcheck</span>
            <p className="text-[15px] font-semibold">
              No photo matches “{lastQuery}”. Check the spelling of {unrecognized.map((w, i) => <span key={w}>{i > 0 && ", "}<i className="text-accent">“{w}”</i></span>)}.
            </p>
            <p className="mt-1 text-[13px] text-ink/65">That isn't an English word or one of your file names, so the picture search wasn't run on it.</p>
          </div>
        )}

        {results && shown.length === 0 && unrecognized.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-ink/65" style={{ fontSize: 40 }}>hide_image</span>
            <p className="text-[15px] font-semibold">No {kind === "all" ? "photo or video" : kind} on this computer matches “{lastQuery}”.</p>
            <p className="mt-1 text-[13px] text-ink/65">{(kind === "video" ? videoCount : kind === "photo" ? photoCount : photoCount + videoCount).toLocaleString()} {kind === "all" ? "photos and videos" : kind + "s"} checked by what's in the picture.</p>
          </div>
        )}

        {results && shown.length > 0 && (
          <>
            <div className="mb-2 flex items-center gap-2">
              {strong.length > 0 ? (
                <>
                  <span className="text-[15px] font-semibold">Best matches</span>
                  <span className="chip chip-accent">{strong.length} {kind === "all" ? "result" : kind}{strong.length === 1 ? "" : "s"}</span>
                </>
              ) : (
                <span className="text-[13px] text-ink/70"><b className="text-ink/90">No confident match for “{lastQuery}”.</b> The photos below are only loosely related.</span>
              )}
              <span className="ml-auto text-[12px] text-ink/65">Matched on this computer in <span className="mono">{elapsed?.toFixed(0)} ms</span></span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
              {strong.map((r) => <PhotoCard key={r.file_id} r={r} strong onError={onError} />)}
            </div>
            {weak.length > 0 && (
              <>
                <div className="my-3 flex items-center gap-3 text-[12px] text-ink/65">
                  Possibly related (weaker matches)<span className="h-px flex-1 bg-ink/[0.07]" />
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
                  {weak.map((r) => <PhotoCard key={r.file_id} r={r} strong={false} onError={onError} />)}
                </div>
              </>
            )}
          </>
        )}

        {!results && !searching && latest && latest.length > 0 && (
          <>
            <div className="mb-2 text-[13px] text-ink/65">Newest first. {photoCount.toLocaleString()} photos and {videoCount.toLocaleString()} videos can be found by what is in them.</div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
              {latest.map((r) => <PhotoCard key={r.file_id} r={r} strong browsing onError={onError} />)}
            </div>
          </>
        )}
        {!results && !searching && latestFailed && !latest && (
          <div role="alert" className="py-16 text-center text-[14px] text-ink/70">Photos could not be listed. {latestFailed}
            <button className="btn-secondary ml-2 px-2 py-0.5 text-[12px]" onClick={() => { setLatestFailed(null); setReloadKey((k) => k + 1); }}>Retry</button>
          </div>
        )}
        {!results && !searching && latest && latest.length === 0 && (
          <div className="py-16 text-center text-[14px] text-ink/65">No photos indexed yet. Add a folder with photos in Index.</div>
        )}
        {!results && !searching && !latest && !latestFailed && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">{Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton aspect-[4/3]" />)}</div>
        )}
      </div>

      <div className="flex items-center gap-4 text-[12px] text-ink/65">
        <span>double-click <span className="kbd">open</span></span>
        <span>right-click <span className="kbd">reveal in {FILE_MANAGER}</span></span>
        <span className="ml-auto">Pictures are matched by a model on this computer</span>
      </div>
    </div>
  );
}

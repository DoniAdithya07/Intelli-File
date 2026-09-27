import { useCallback, useEffect, useState } from "react";
import { formatTimestamp, search, searchVisual, suggestVisual, thumbnailUrl, VisualKind, VisualSearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER } from "../platform";
import { PageHeader, Tabs } from "../ui/kit";
import { Omnibox } from "../ui/Omnibox";
import { openResult, revealResult } from "../ui/ResultCard";

interface Props {
  photoCount: number;
  videoCount: number;
  onError: (m: string | null) => void;
}

const KINDS: { id: VisualKind; label: string }[] = [
  { id: "all", label: "All" },
  { id: "photo", label: "Photos" },
  { id: "video", label: "Videos" },
];

function PhotoCard({ r, strong, onError, browsing }: { r: VisualSearchResult; strong: boolean; onError: (m: string) => void; browsing?: boolean }) {
  return (
    <div
      className={`panel group cursor-default overflow-hidden ${strong || browsing ? "" : "opacity-70"}`}
      onDoubleClick={() => openResult(r.path, onError, r.file_id)}
      title={`Double-click to open. Right-click to show in ${FILE_MANAGER}.`}
      onContextMenu={(e) => { e.preventDefault(); revealResult(r.path, onError, r.file_id); }}
    >
      <div className="relative aspect-[4/3] bg-ink/[0.03]">
        {r.path && <img src={thumbnailUrl(r.path, 320, r.timestamp_offset_seconds)} alt={r.filename} className="h-full w-full object-cover" loading="lazy" />}
        {!browsing && (
          <span className="absolute left-2 top-2 rounded bg-black/70 px-2 py-0.5 text-[12px] text-white">{strong ? "Strong match" : "Weaker match"}</span>
        )}
        {r.kind === "video" && r.timestamp_offset_seconds != null && (
          <span className="mono absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-0.5 text-[12px] text-white" title="The moment that matched. Open the video and go to this time.">
            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>play_arrow</span>
            {formatTimestamp(r.timestamp_offset_seconds)}
          </span>
        )}
        <div className="absolute inset-x-0 bottom-0 flex justify-end gap-1 p-2 opacity-0 transition-opacity group-hover:opacity-100">
          <button className="rounded bg-black/65 px-1.5 py-0.5 text-[12px] text-white hover:bg-black/80" onClick={() => openResult(r.path, onError, r.file_id)}>Open</button>
          <button className="rounded bg-black/65 px-1.5 py-0.5 text-[12px] text-white hover:bg-black/80" onClick={() => revealResult(r.path, onError, r.file_id)}>Show in {FILE_MANAGER}</button>
        </div>
      </div>
      {r.kind === "video" && r.path && r.moments && r.moments.length > 1 && (
        <div className="flex gap-1 px-2 pt-2" title="The moments that matched">
          {r.moments.map((m) => (
            <div key={m.t} className="relative min-w-0 flex-1 overflow-hidden rounded-md bg-black/40">
              <img src={thumbnailUrl(r.path!, 160, m.t)} alt="" className="aspect-video w-full object-cover" loading="lazy" />
              <span className="mono absolute bottom-0.5 right-1 rounded bg-black/70 px-1 text-[11px] text-white">{formatTimestamp(m.t)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="px-3 py-2">
        <div className="truncate text-[13px] font-medium">{r.filename}</div>
        <div className="mono mt-0.5 flex justify-between text-[12px] text-ink/60">
          <span>{r.captured_at ? new Date(r.captured_at).toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" }) : "No date"}</span>
          <span>{r.kind ?? "photo"}</span>
        </div>
      </div>
    </div>
  );
}

export function PhotosPage({ photoCount, videoCount, onError }: Props) {
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

  const run = useCallback(async (q?: string, k?: VisualKind) => {
    const text = (q ?? query).trim();
    if (!text || searching) return;
    setSearching(true);
    onError(null);
    const t0 = performance.now();
    try {
      const res = await searchVisual(text, k ?? kind);
      if (res.error) { onError(res.error); setResults(null); }
      else {
        setResults(res.results ?? []);
        setUnrecognized(res.unrecognized ?? []);
        setLastQuery(res.corrected_query ?? text);
        setElapsed(performance.now() - t0);
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  }, [query, kind, searching, onError]);

  const voice = useVoice(useCallback((t: string) => setQuery(t), []), useCallback((m: string) => onError(m), [onError]));

  // Before a search: the newest photos and videos in the index, as they are.
  const [latest, setLatest] = useState<VisualSearchResult[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    const words = kind === "photo" ? ["type:image"] : kind === "video" ? ["type:video"] : ["type:image", "type:video"];
    Promise.all(words.map((w) => search(w, "auto", 40, undefined, false)))
      .then((all) => {
        if (cancelled) return;
        const files = all.flatMap((r) => r.results).sort((a, b) => b.modified_time - a.modified_time).slice(0, 40);
        setLatest(files.map((f) => ({
          file_id: f.file_id, path: f.path, filename: f.filename, score: 0, confidence: "strong",
          kind: /\.(mp4|mov|m4v|mkv|webm|avi)$/i.test(f.filename) ? "video" : "photo",
          captured_at: new Date(f.modified_time * 1000).toISOString(), timestamp_offset_seconds: null, moments: null,
        })));
      })
      .catch(() => { if (!cancelled) setLatest([]); });
    return () => { cancelled = true; };
  }, [kind]);
  const strong = (results ?? []).filter((r) => r.confidence !== "weak");
  const weak = (results ?? []).filter((r) => r.confidence === "weak");

  return (
    <div className="flex h-full flex-col gap-3">
      <PageHeader title="Photos and videos" subtitle="Find pictures and video moments by describing what is in them." />
      <div>
        <Omnibox value={query} onChange={(v) => { setQuery(v); if (!v.trim()) setResults(null); }} onSubmit={() => run()} placeholder="Describe a photo" icon="image_search" searching={searching} voice={voice} autoFocus />
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-ink/70">
            Did you mean
            <button className="btn-secondary px-2 py-0.5 text-[13px] font-medium text-accent" onClick={() => { setQuery(suggestion); setSuggestion(null); run(suggestion); }}>{suggestion}</button>
          </div>
        )}
        <div className="mt-3 flex items-end justify-between gap-3 border-b border-rule">
          <Tabs label="Photo or video" tabs={KINDS} value={kind} onChange={(k) => { setKind(k); if (lastQuery && results) run(lastQuery, k); }} />
          <span className="pb-2 text-[12px] text-ink/60">A short phrase works best: <i>a person in a red shirt</i></span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {searching && !results && <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton aspect-[4/3]" />)}</div>}

        {results && results.length === 0 && unrecognized.length > 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-ink/55" style={{ fontSize: 40 }}>spellcheck</span>
            <p className="text-[15px] font-semibold">
              No photo matches “{lastQuery}”. Check the spelling of {unrecognized.map((w, i) => <span key={w}>{i > 0 && ", "}<i className="text-accent">“{w}”</i></span>)}.
            </p>
            <p className="mt-1 text-[13px] text-ink/60">That isn't an English word or one of your file names, so the picture search wasn't run on it.</p>
          </div>
        )}

        {results && results.length === 0 && unrecognized.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-ink/55" style={{ fontSize: 40 }}>hide_image</span>
            <p className="text-[15px] font-semibold">No {kind === "all" ? "photo or video" : kind} on this computer matches “{lastQuery}”.</p>
            <p className="mt-1 text-[13px] text-ink/60">{(kind === "video" ? videoCount : kind === "photo" ? photoCount : photoCount + videoCount).toLocaleString()} {kind === "all" ? "photos and videos" : kind + "s"} checked by what's in the picture.</p>
          </div>
        )}

        {results && results.length > 0 && (
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
              <span className="ml-auto text-[12px] text-ink/60">Matched on this computer in <span className="mono">{elapsed?.toFixed(0)} ms</span></span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
              {strong.map((r) => <PhotoCard key={r.file_id} r={r} strong onError={onError} />)}
            </div>
            {weak.length > 0 && (
              <>
                <div className="my-3 flex items-center gap-3 text-[12px] text-ink/60">
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
        {!results && !searching && latest && latest.length === 0 && (
          <div className="py-16 text-center text-[14px] text-ink/65">No photos indexed yet. Add a folder with photos in Index.</div>
        )}
        {!results && !searching && !latest && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">{Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton aspect-[4/3]" />)}</div>
        )}
      </div>

      <div className="flex items-center gap-4 text-[12px] text-ink/60">
        <span>double-click <span className="kbd">open</span></span>
        <span>right-click <span className="kbd">reveal in {FILE_MANAGER}</span></span>
        <span className="ml-auto">Pictures are matched by a model on this computer</span>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { formatTimestamp, searchVisual, suggestVisual, thumbnailUrl, VisualKind, VisualSearchResult } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { FILE_MANAGER } from "../platform";
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

// CLIP score in the API is squared L2 (0 = identical, 2 = opposite); show it as a
// match percentage so it reads like the design ("98% match"): cos = 1 - d/2.
function matchPercent(distance: number): number {
  const cos = 1 - distance / 2;
  // Real CLIP cosines for good matches sit around 0.25-0.35; stretch that band to 60-100.
  return Math.round(Math.max(0, Math.min(100, 60 + (cos - 0.2) * 250)));
}

function PhotoCard({ r, strong, onError }: { r: VisualSearchResult; strong: boolean; onError: (m: string) => void }) {
  return (
    <div
      className={`panel group cursor-default overflow-hidden ${strong ? "border-accent/60" : "opacity-60"}`}
      onDoubleClick={() => openResult(r.path, onError, r.file_id)}
      title={`Double-click to open · right-click for ${FILE_MANAGER}`}
      onContextMenu={(e) => { e.preventDefault(); revealResult(r.path, onError, r.file_id); }}
    >
      <div className="relative aspect-[4/3] bg-white/[0.03]">
        {r.path && <img src={thumbnailUrl(r.path, 320, r.timestamp_offset_seconds)} alt={r.filename} className="h-full w-full object-cover" loading="lazy" />}
        <span className={`mono absolute left-2 top-2 rounded-md px-2 py-0.5 text-[11px] ${strong ? "bg-black/60 text-accent" : "bg-black/60 text-white/60"}`}>
          {strong && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" />}
          {matchPercent(r.score)}% match
        </span>
        {r.kind === "video" && r.timestamp_offset_seconds != null && (
          <span className="mono absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-0.5 text-[11px] text-secondary" title="The moment that matched — open the video and scrub to this time">
            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>play_arrow</span>
            {formatTimestamp(r.timestamp_offset_seconds)}
          </span>
        )}
        <div className="absolute inset-x-0 bottom-0 flex justify-end gap-1 p-2 opacity-0 transition-opacity group-hover:opacity-100">
          <button className="kbd bg-black/60 hover:text-white" onClick={() => openResult(r.path, onError, r.file_id)}>Open</button>
          <button className="kbd bg-black/60 hover:text-white" onClick={() => revealResult(r.path, onError, r.file_id)}>{FILE_MANAGER}</button>
        </div>
      </div>
      {r.kind === "video" && r.path && r.moments && r.moments.length > 1 && (
        <div className="flex gap-1 px-2 pt-2" title="The moments that matched — scrub to any of these">
          {r.moments.map((m) => (
            <div key={m.t} className="relative min-w-0 flex-1 overflow-hidden rounded-md bg-black/40">
              <img src={thumbnailUrl(r.path!, 160, m.t)} alt="" className="aspect-video w-full object-cover" loading="lazy" />
              <span className="mono absolute bottom-0.5 right-1 rounded bg-black/70 px-1 text-[10px] text-white/85">{formatTimestamp(m.t)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="px-3 py-2">
        <div className="truncate text-[13px] font-medium">{r.filename}</div>
        <div className="mono mt-0.5 flex justify-between text-[11px] text-white/40">
          <span>{r.captured_at ? new Date(r.captured_at).toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" }) : "—"}</span>
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
  const strong = (results ?? []).filter((r) => r.confidence !== "weak");
  const weak = (results ?? []).filter((r) => r.confidence === "weak");

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="panel p-3">
        <Omnibox value={query} onChange={setQuery} onSubmit={() => run()} placeholder="a cat in the snow · a plane parked on a runway · a birthday cake with candles" icon="image_search" searching={searching} voice={voice} autoFocus />
        {suggestion && voice.state === "idle" && (
          <div className="mt-2 flex items-center gap-2 text-[13px] text-white/60">
            <span className="material-symbols-outlined icon-sm text-accent">spellcheck</span>
            Did you mean:
            <button
              className="chip chip-accent hover:brightness-125"
              onClick={() => { setQuery(suggestion); setSuggestion(null); run(suggestion); }}
              title="Replace the query with this spelling and search"
            >
              {suggestion}
            </button>
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-[13px] text-white/60">
            <span className="material-symbols-outlined icon-sm text-accent">auto_awesome</span>
            Describe the scene as a short phrase — <i className="text-white/80">"a person wearing a red shirt"</i> beats <i className="text-white/80">"red shirt"</i>.
          </p>
          <div className="flex gap-1 rounded-lg bg-white/[0.03] p-1" role="tablist" aria-label="Photo or video">
            {KINDS.map((k) => (
              <button
                key={k.id}
                role="tab"
                aria-selected={kind === k.id}
                className={`mono rounded-md px-3 py-1 text-[11px] transition-colors ${kind === k.id ? "bg-accent/15 text-accent" : "text-white/55 hover:text-white"}`}
                onClick={() => { setKind(k.id); if (lastQuery) run(lastQuery, k.id); }}
              >
                {k.id === "video" && <span className="material-symbols-outlined mr-1 align-middle" style={{ fontSize: 13 }}>play_arrow</span>}
                {k.label}
                {k.id === "video" && videoCount > 0 && <span className="ml-1 text-white/40">{videoCount}</span>}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {searching && !results && <div className="grid grid-cols-4 gap-3">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton aspect-[4/3]" />)}</div>}

        {results && results.length === 0 && unrecognized.length > 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-white/30" style={{ fontSize: 40 }}>spellcheck</span>
            <p className="text-[15px] font-semibold">
              No photo matches “{lastQuery}” — check the spelling of {unrecognized.map((w, i) => <span key={w}>{i > 0 && ", "}<i className="text-accent">“{w}”</i></span>)}.
            </p>
            <p className="mt-1 text-[13px] text-white/50">That isn't an English word or one of your file names, so the picture search wasn't run on it.</p>
          </div>
        )}

        {results && results.length === 0 && unrecognized.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <span className="material-symbols-outlined mb-3 text-white/30" style={{ fontSize: 40 }}>hide_image</span>
            <p className="text-[15px] font-semibold">No {kind === "all" ? "photo or video" : kind} on this computer matches “{lastQuery}”.</p>
            <p className="mt-1 text-[13px] text-white/50">{(kind === "video" ? videoCount : kind === "photo" ? photoCount : photoCount + videoCount).toLocaleString()} {kind === "all" ? "photos and videos" : kind + "s"} checked by what's in the picture.</p>
          </div>
        )}

        {results && results.length > 0 && (
          <>
            <div className="mb-2 flex items-center gap-2">
              {strong.length > 0 ? (
                <>
                  <span className="text-[15px] font-semibold">Top matches (high confidence)</span>
                  <span className="chip chip-accent">{strong.length} {kind === "all" ? "result" : kind}{strong.length === 1 ? "" : "s"}</span>
                </>
              ) : (
                <span className="text-[13px] text-white/60"><b className="text-white/85">No confident match for “{lastQuery}”.</b> The photos below are only loosely related.</span>
              )}
              <span className="mono ml-auto text-[11px] text-white/40">CLIP ViT-B/16 fp16 · {elapsed?.toFixed(0)} ms</span>
            </div>
            <div className="rise grid grid-cols-4 gap-3">
              {strong.map((r) => <PhotoCard key={r.file_id} r={r} strong onError={onError} />)}
            </div>
            {weak.length > 0 && (
              <>
                <div className="mono my-3 flex items-center gap-3 text-[11px] uppercase tracking-wider text-white/40">
                  Possibly related (weaker matches)<span className="h-px flex-1 bg-white/[0.07]" />
                </div>
                <div className="rise grid grid-cols-4 gap-3">
                  {weak.map((r) => <PhotoCard key={r.file_id} r={r} strong={false} onError={onError} />)}
                </div>
              </>
            )}
          </>
        )}

        {!results && !searching && (
          <div className="flex flex-col items-center justify-center py-16 text-center text-white/45">
            <span className="material-symbols-outlined mb-3 text-white/25" style={{ fontSize: 40 }}>photo_library</span>
            <p className="text-[14px]">{photoCount.toLocaleString()} photos and {videoCount.toLocaleString()} videos are searchable by what's in them — no tags, no captions, no file names.</p>
          </div>
        )}
      </div>

      <div className="mono flex items-center gap-4 text-[11px] text-white/45">
        <span>double-click <span className="kbd">open</span></span>
        <span>right-click <span className="kbd">reveal in {FILE_MANAGER}</span></span>
        <span className="ml-auto"><span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" />Local CLIP ViT-B/16 · 512-dim</span>
      </div>
    </div>
  );
}

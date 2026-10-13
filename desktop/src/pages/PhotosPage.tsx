import { useCallback, useEffect, useMemo, useState } from "react";
import { describeError, formatTimestamp, searchVisual, suggestVisual, VisualKind, VisualSearchResult } from "../backend";
import { Notify, useActions } from "../lib/actions";
import { useStatusBar } from "../shell/ctx";
import { Banner, CommandBar, CommandButton, EmptyState, Fi, G, Menu, MenuItem, Pills } from "../ui/controls";
import { FileGrid } from "../ui/FileGrid";
import { canOpen, openResult, revealResult } from "../ui/ResultCard";
import { Thumb } from "../ui/Thumb";

interface Props {
  query: string;
  /** Changes on every Enter, so the same words search again. */
  run: number;
  engineReady: boolean;
  photoCount: number;
  videoCount: number;
  available: boolean; // false when the photo and video model did not load
  notify: Notify;
  onError: (m: string | null) => void;
  onRunQuery: (q: string) => void;
  preview: boolean;
  onPreview: (b: boolean) => void;
}

const KINDS: { id: VisualKind; label: string }[] = [
  { id: "all", label: "All" },
  { id: "photo", label: "Photos" },
  { id: "video", label: "Videos" },
];
const SIZES = { small: 120, medium: 160, large: 220 } as const;
const EXAMPLES = ["a person in a red shirt", "a dog on a beach", "a whiteboard with notes"];
const dateOf = (r: VisualSearchResult) => (r.captured_at ? new Date(r.captured_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "No date");

/** Photos and videos found by what is in them. Group headers and one small label say how sure the match is: no label on every tile. */
export function PhotosPage({ query, run, engineReady, photoCount, videoCount, available, notify, onError, onRunQuery, preview, onPreview }: Props) {
  const text = query.trim();
  const [kind, setKind] = useState<VisualKind>("all");
  const [results, setResults] = useState<VisualSearchResult[] | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [unrecognized, setUnrecognized] = useState<string[]>([]);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [size, setSize] = useState<keyof typeof SIZES>("medium");
  const [by, setBy] = useState<"match" | "date">("match");
  const [selected, setSelected] = useState<VisualSearchResult[]>([]);
  const [ctx, setCtx] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [weakOpen, setWeakOpen] = useState(true);
  const actions = useActions(() => undefined, notify, [], () => undefined);

  // "Did you mean", against the photo-query checker.
  useEffect(() => {
    if (!text) { setSuggestion(null); return; }
    let cancelled = false;
    suggestVisual(text).then((res) => { if (!cancelled) setSuggestion(res.suggestion && res.suggestion !== text ? res.suggestion : null); }).catch(() => { if (!cancelled) setSuggestion(null); });
    return () => { cancelled = true; };
  }, [text, run]);

  // Another query, Enter, or another kind cancels the search in flight; its reply is ignored.
  useEffect(() => {
    if (!text) { setResults(null); setSearching(false); return; }
    if (!available || !engineReady) return;
    const ctl = new AbortController();
    setSearching(true);
    onError(null);
    const t0 = performance.now();
    searchVisual(text, kind, 48, ctl.signal)
      .then((res) => {
        if (ctl.signal.aborted) return;
        if (res.error) { onError(res.error); setResults(null); return; }
        setResults(res.results ?? []);
        setUnrecognized(res.unrecognized ?? []);
        setLastQuery(res.corrected_query ?? text);
        setElapsed(performance.now() - t0);
        setWeakOpen(!(res.results ?? []).some((r) => r.confidence !== "weak"));
      })
      .catch((e) => { if (!ctl.signal.aborted) onError(describeError(e) || null); })
      .finally(() => { if (!ctl.signal.aborted) setSearching(false); });
    return () => ctl.abort();
  }, [text, run, kind, available, engineReady, onError]);

  const { strong, weak } = useMemo(() => {
    const shown = (results ?? []).filter((r) => kind === "all" || (r.kind ?? "photo") === kind); // a tab never shows the other kind
    const order = (l: VisualSearchResult[]) => (by === "date" ? [...l].sort((a, b) => (b.captured_at ?? "").localeCompare(a.captured_at ?? "")) : l);
    return { strong: order(shown.filter((r) => r.confidence !== "weak")), weak: order(shown.filter((r) => r.confidence === "weak")) };
  }, [results, kind, by]);
  const total = strong.length + weak.length;
  const px = SIZES[size];

  useStatusBar({ left: searching ? "Searching..." : results ? `${total} result${total === 1 ? "" : "s"}${elapsed !== null ? ` in ${Math.round(elapsed)} ms` : ""}${selected.length ? `   ${selected.length} selected` : ""}` : `${photoCount.toLocaleString()} photos and ${videoCount.toLocaleString()} videos indexed`, busy: searching });

  const entry = (r: VisualSearchResult) => ({ path: r.path ?? "", name: r.filename, is_dir: false, indexed: true, file_id: r.file_id });
  const onContext = useCallback((rows: VisualSearchResult[], x: number, y: number) => setCtx({ x, y, items: actions.menu(rows.map(entry)) }), [actions]);

  const tile = (r: VisualSearchResult, isWeak: boolean) => (
    <div style={{ width: px }}>
      <div className="relative overflow-hidden rounded" style={{ width: px, height: px }}>
        {r.path ? <Thumb path={r.path} size={Math.max(240, px * 2)} at={r.timestamp_offset_seconds} alt="" className="h-full w-full object-cover" boxClassName="h-full w-full" /> : <div className="grid h-full place-items-center text-[12px] text-sub">No preview</div>}
        {isWeak && <span className="absolute left-1 top-1 rounded bg-black/70 px-2 py-0.5 text-[12px] text-white">Possible match</span>}
        {r.kind === "video" && r.timestamp_offset_seconds != null && (
          <span className="absolute bottom-1 right-1 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-[12px] text-white" title="The moment that matched"><Fi g={G.play} size={10} />{formatTimestamp(r.timestamp_offset_seconds)}</span>
        )}
      </div>
      <div className="mt-1 truncate text-[14px]" title={r.filename}>{r.filename}</div>
      <div className="text-[12px] text-sub">{dateOf(r)}</div>
    </div>
  );
  const grid = (list: VisualSearchResult[], isWeak: boolean, label: string) => (
    <FileGrid label={label} items={list.map((r) => ({ key: `${r.file_id}:${r.timestamp_offset_seconds ?? ""}`, data: r }))} columns={`repeat(auto-fill, ${px + 8}px)`} gap={8}
      tileLabel={(r) => `${r.filename}, ${dateOf(r)}${isWeak ? ", possible match" : ""}`} getName={(r) => r.filename}
      onOpen={(r) => openResult(r.path, (m) => onError(m), r.file_id)} onReveal={(r) => revealResult(r.path, (m) => onError(m), r.file_id)}
      onCopyPaths={(rows) => actions.copyPaths(rows.map(entry))} onContext={onContext} onSelection={setSelected} renderTile={(r) => tile(r, isWeak)} />
  );

  const one = selected.length === 1 ? selected[0] : null;
  const unavailable = !available;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar>
        <CommandButton icon={G.sort} label="Sort" menu={{ items: [
          { id: "match", label: "Best match", checked: by === "match", onClick: () => setBy("match") },
          { id: "date", label: "Date taken or modified", checked: by === "date", onClick: () => setBy("date") },
        ] }} />
        <CommandButton icon={G.view} label="Size" menu={{ items: (Object.keys(SIZES) as (keyof typeof SIZES)[]).map((s) => ({ id: s, label: s[0].toUpperCase() + s.slice(1), checked: size === s, onClick: () => setSize(s) })) }} />
        <span className="flex-1" />
        <CommandButton icon={G.pane} label="Preview" pressed={preview} onClick={() => onPreview(!preview)} title="Preview pane (Alt+P)" />
      </CommandBar>
      {unavailable && <Banner tone="warn" title="Searching by what is in a picture is not available.">The photo and video model did not load. Extract IntelliFile-windows.zip again, completely, then reopen IntelliFile. Photos can still be found by file name on the Search page.</Banner>}
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-rule px-4 py-2">
        <Pills label="Photo or video" value={kind} onChange={setKind} items={KINDS} />
        <span className="text-[12px] text-sub">A short phrase works best: a person in a red shirt</span>
      </div>
      {suggestion && <Banner tone="info" action={{ label: `Search for "${suggestion}" instead`, onClick: () => onRunQuery(suggestion) }}>Did you mean <b className="font-semibold">{suggestion}</b>?</Banner>}

      <div className="flex min-h-0 flex-1">
        <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${searching && results ? "opacity-60" : ""}`} aria-busy={searching || undefined}>
          {!text && (
            <div className="mx-auto w-full max-w-[640px] px-6 py-10">
              <h2 className="text-[16px] font-semibold">Describe what is in the picture</h2>
              <p className="mt-1 text-[14px] text-sub">Type in the search box above, for example: a person in a red shirt.</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {EXAMPLES.map((ex) => <button key={ex} className="pill text-[14px]" disabled={unavailable} onClick={() => onRunQuery(ex)}>{ex}</button>)}
              </div>
              <p className="mt-6 text-[14px] text-sub">{photoCount.toLocaleString()} photos and {videoCount.toLocaleString()} videos indexed.</p>
            </div>
          )}
          {text && !results && !searching && !unavailable && !engineReady && <EmptyState title="Waiting for the search engine..." text="Your search runs by itself as soon as it is ready." />}
          {text && searching && !results && (
            <div className="flex flex-wrap gap-2 p-3" aria-hidden>{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton" style={{ width: px, height: px }} />)}</div>
          )}
          {results && total === 0 && unrecognized.length > 0 && (
            <EmptyState title={`No pictures matched "${lastQuery}".`} text={<>Check the spelling of {unrecognized.map((w, i) => <span key={w}>{i > 0 && ", "}<i className="text-accent">"{w}"</i></span>)}. That isn't an English word or one of your file names, so the picture search wasn't run on it.</>} />
          )}
          {results && total === 0 && unrecognized.length === 0 && (
            <EmptyState title={`No pictures matched "${lastQuery}".`} text="Try describing what is in the picture, for example: a dog on a beach." />
          )}
          {total > 0 && (
            <div className="min-h-0 flex-1 overflow-y-auto">
              {strong.length > 0 ? (
                <>
                  <h2 className="px-4 pt-3 text-[14px] font-semibold">Best matches ({strong.length})</h2>
                  <div className="flex flex-col">{grid(strong, false, "Best matches")}</div>
                </>
              ) : <p className="px-4 pt-3 text-[14px] text-sub"><b className="font-semibold text-ink">No best match for "{lastQuery}".</b> The pictures below are only possible matches.</p>}
              {weak.length > 0 && (
                <>
                  <button className="mx-3 mt-2 flex h-8 items-center gap-2 rounded px-1 text-[14px] font-semibold hover:bg-[var(--hover)]" aria-expanded={weakOpen} onClick={() => setWeakOpen((o) => !o)}>
                    <Fi g={weakOpen ? G.down : G.right} size={12} />Possible matches ({weak.length})
                  </button>
                  {weakOpen && <div className="flex flex-col">{grid(weak, true, "Possible matches")}</div>}
                </>
              )}
              <p className="px-4 pb-4 pt-2 text-[12px] text-sub">Pictures are matched by a model on this computer, so some results may be wrong.</p>
            </div>
          )}
        </div>
        {preview && (
          <aside aria-label="Preview" className="w-[360px] shrink-0 overflow-y-auto border-l border-rule bg-content">
            {!one ? <div className="grid h-full place-items-center px-6 text-center text-[14px] text-sub">{selected.length > 1 ? `${selected.length} items selected` : "Select a picture or video to see it larger."}</div> : (
              <div className="flex h-full flex-col">
                <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                  {one.path && <Thumb path={one.path} size={480} at={one.timestamp_offset_seconds} alt={`Preview of ${one.filename}`} className="max-h-[260px] w-full rounded object-contain" boxClassName="h-[220px] w-full" />}
                  <h2 className="mt-3 break-words text-[14px] font-semibold select-text">{one.filename}</h2>
                  <p className="text-[12px] text-sub">{one.confidence === "weak" ? "Possible match" : "Best match"}</p>
                  <p className="mt-1 text-[14px] text-sub">{dateOf(one)}</p>
                  {one.path && <p className="mono mt-1 break-all text-[12px] select-text">{one.path.replace(/[\\/][^\\/]*$/, "")}</p>}
                  {one.kind === "video" && one.path && one.moments && one.moments.length > 0 && (
                    <>
                      <h3 className="mt-4 text-[12px] text-dim">Moments that matched</h3>
                      <div className="mt-1 flex gap-2">
                        {one.moments.map((m) => (
                          <div key={m.t} className="relative min-w-0 flex-1 overflow-hidden rounded bg-black/40">
                            <Thumb path={one.path!} size={160} at={m.t} alt="" className="aspect-video w-full object-cover" />
                            <span className="absolute bottom-0.5 right-1 rounded bg-black/70 px-1 text-[12px] text-white">{formatTimestamp(m.t)}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
                <div className="flex shrink-0 gap-2 border-t border-rule px-5 py-3">
                  {canOpen(one.path) && <button className="btn-primary h-8 px-4 text-[14px]" onClick={() => openResult(one.path, (m) => onError(m), one.file_id)}>Open</button>}
                  <button className="btn-secondary h-8 px-3 text-[14px]" onClick={() => revealResult(one.path, (m) => onError(m), one.file_id)}>Show in Explorer</button>
                </div>
              </div>
            )}
          </aside>
        )}
      </div>
      {ctx && <Menu x={ctx.x} y={ctx.y} items={ctx.items} label="Picture actions" onClose={() => setCtx(null)} />}
    </div>
  );
}


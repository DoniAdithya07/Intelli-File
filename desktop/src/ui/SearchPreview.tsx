import { useEffect, useState } from "react";
import { fileKind, formatAgo, formatBytes, getPassages, Passage, PassagesResponse, SearchResult, shortenFolder, thumbnailUrl } from "../backend";
import { FILE_MANAGER } from "../platform";
import { FileBadge, openResult, revealResult } from "./ResultCard";

// Words too common to be worth marking in a passage.
const STOP = new Set("the and for with that this from what when where which who how are was were have has had not you your our their about into over under after before than then them they its".split(" "));

/** The words to mark in a passage: the ones the keyword index matched (its **marks**) plus the query's own words. */
export function highlightTerms(query: string, matchedChunk: string): string[] {
  const marked = [...matchedChunk.matchAll(/\*\*([^*]+)\*\*/g)].map((m) => m[1]);
  const typed = query
    .replace(/\b(type|ext|after|before|size|in):\S+/gi, " ")
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length >= 3 && !STOP.has(w.toLowerCase()));
  const all = [...marked, ...typed].map((w) => w.trim()).filter(Boolean);
  return [...new Set(all.map((w) => w.toLowerCase()))].sort((a, b) => b.length - a.length);
}

function Marked({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const esc = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // Whole words only: "add" must not light up inside "additional".
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${esc.join("|")})(?![\\p{L}\\p{N}])`, "giu");
  const parts = text.split(re);
  return <>{parts.map((p, i) => (i % 2 === 1 ? <mark className="hl" key={i}>{p}</mark> : <span key={i}>{p}</span>))}</>;
}

function unitLabel(filename: string, page: number | null): string {
  if (page == null) return "";
  return `, ${fileKind(filename).group === "slides" ? "slide" : "page"} ${page}`;
}

// Reasons that come from the user's own habits rather than the file (Phase 17).
const PERSONAL_PREFIXES = ["You use this", "You used to", "Your usual", "Your most-used"];

function PassageBlock({ title, passage, terms, filename, italic }: { title: string; passage: Passage; terms: string[]; filename: string; italic: boolean }) {
  return (
    <section className="mt-4">
      <h3 className="text-[13px] font-medium text-ink/70">{title}{unitLabel(filename, passage.page)}</h3>
      {passage.heading && <div className="mt-1 text-[13px] font-medium">{passage.heading}</div>}
      <p className={`mt-1.5 whitespace-pre-line border-l-2 border-rule-strong pl-3 text-[14px] leading-[1.6] text-ink/90 select-text ${italic ? "italic" : ""}`}>
        <Marked text={passage.text.trim()} terms={terms} />
      </p>
    </section>
  );
}

interface Props {
  result: SearchResult | null;
  query: string;
  onError: (m: string) => void;
}

/**
 * The right-hand pane: what the file says where it matched, the passage
 * after it, and the reasons the backend gave. Only indexed text is shown;
 * there is no imitation of a Word or PDF page.
 */
export function SearchPreview({ result, query, onError }: Props) {
  const [data, setData] = useState<PassagesResponse | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "failed">("idle");

  useEffect(() => {
    if (!result) { setData(null); setState("idle"); return; }
    const controller = new AbortController();
    setState("loading");
    getPassages(result.file_id, result.chunk_id, controller.signal)
      .then((d) => { setData(d); setState("ready"); })
      .catch((e) => { if (!(e instanceof DOMException && e.name === "AbortError")) setState("failed"); });
    return () => controller.abort();
  }, [result?.file_id, result?.chunk_id]);

  if (!result) {
    return (
      <div className="grid h-full place-items-center px-6 text-center text-[13px] text-ink/60">
        Select a result to see the matching passage.
      </div>
    );
  }

  const kind = fileKind(result.filename).group;
  const terms = highlightTerms(query, result.matched_chunk || "");
  const weak = result.confidence === "weak";
  const gone = state === "ready" && data?.error === "not_indexed";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div key={`${result.file_id}:${result.chunk_id ?? ""}`} className="anim-fade min-h-0 flex-1 overflow-y-auto px-5 pb-4 pt-4">
        <div className="flex items-start gap-2.5">
          <FileBadge filename={result.filename} />
          <div className="min-w-0">
            <h2 className="break-words text-[15px] font-semibold leading-snug select-text">{result.filename}</h2>
            {result.path && <div className="mono mt-0.5 truncate text-[12px] text-ink/60" title={result.path}>in {shortenFolder(result.path)}</div>}
            <div className="mt-0.5 text-[12px] text-ink/60"><span className="mono">{formatBytes(result.size)}</span>, modified {formatAgo(result.modified_time)}</div>
          </div>
        </div>

        {kind === "image" && result.path && (
          <img src={thumbnailUrl(result.path, 480)} alt={`Picture: ${result.filename}`} className="mt-4 max-h-[220px] w-auto rounded border border-rule object-contain" />
        )}

        {state === "loading" && <p className="mt-4 text-[13px] text-ink/60">Loading passage</p>}
        {state === "failed" && <p className="mt-4 text-[13px] text-error">The passage could not be loaded. The search engine did not answer; try selecting the result again.</p>}
        {gone && (
          <div className="mt-4 text-[13px]">
            <p className="text-error">This file could not be read: it is no longer in the index. It may have been moved, renamed or deleted since the search.</p>
            <button className="btn-secondary mt-2 px-3 py-1 text-[13px]" onClick={() => revealResult(result.path, onError, result.file_id)}>Show in {FILE_MANAGER}</button>
          </div>
        )}
        {state === "ready" && data && !data.error && !data.match && (
          <p className="mt-4 text-[13px] text-ink/60">
            {kind === "image" ? "No text was found in this picture. It matched by its file name." : kind === "audio" ? "This recording has no transcript yet. It matched by its file name." : "IntelliFile holds no text for this file. It matched by its file name."}
          </p>
        )}
        {state === "ready" && data?.match && (
          <>
            <PassageBlock
              title={kind === "image" ? "Text in the picture" : kind === "audio" ? "Spoken passage" : result.chunk_id ? "Matching passage" : "Start of the file"}
              passage={data.match}
              terms={terms}
              filename={result.filename}
              italic={kind === "audio"}
            />
            {data.next && <PassageBlock title="Next passage" passage={data.next} terms={terms} filename={result.filename} italic={kind === "audio"} />}
          </>
        )}

        <section className="mt-5 border-t border-rule pt-4">
          <h3 className="text-[13px] font-semibold">Why this file?</h3>
          <ul className="mt-1.5 space-y-1 text-[13px]">
            {result.why.map((w, i) => {
              const personal = PERSONAL_PREFIXES.some((p) => w.startsWith(p));
              return (
                <li key={i} className="flex gap-2">
                  <span className={`material-symbols-outlined mt-px icon-sm ${personal ? "text-amber" : "text-ink/55"}`} aria-hidden>{personal ? "person" : "check"}</span>
                  <span className={personal ? "text-[rgb(var(--c-amber-text))]" : "text-ink/85"}>{w}</span>
                </li>
              );
            })}
            <li className="flex gap-2">
              <span className="material-symbols-outlined mt-px icon-sm text-ink/55" aria-hidden>{weak ? "remove" : "check"}</span>
              <span className="text-ink/85">{weak ? "Weaker match: only loosely related to the search" : "Strong match"}</span>
            </li>
          </ul>
        </section>
      </div>

      <div className="flex shrink-0 gap-2 border-t border-rule px-5 py-3">
        <button className="btn-primary px-3 py-1.5 text-[13px]" onClick={() => openResult(result.path, onError, result.file_id)}>Open</button>
        <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={() => revealResult(result.path, onError, result.file_id)}>Show in {FILE_MANAGER}</button>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { AskEvent, AskSource, askStream, RouteReport, shortenPath } from "../backend";
import { FILE_MANAGER } from "../platform";
import { FileBadge, openResult, revealResult } from "./ResultCard";

interface Props {
  question: string;
  onError: (m: string | null) => void;
  compact?: boolean;
}

type TraceItem =
  | { kind: "thought"; text: string; system?: boolean }
  | { kind: "call"; tool: string; args: Record<string, unknown>; n: number }
  | { kind: "result"; tool: string; sources: AskSource[]; route?: RouteReport; n: number };

/**
 * Phase 19 — Ask mode. The agent's trace streams in as it plans, calls
 * search as a tool and reads results; then the answer streams token by
 * token and ends with the files it cited (Open / Reveal). Everything
 * shown here came from the local model and the local index.
 */
export function AskPanel({ question, onError, compact }: Props) {
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [answer, setAnswer] = useState("");
  const [final, setFinal] = useState<Extract<AskEvent, { type: "answer" }> | null>(null);
  const [done, setDone] = useState<Extract<AskEvent, { type: "done" }> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(true);
  const [quick, setQuick] = useState<Extract<AskEvent, { type: "quick_answer" }> | null>(null);
  const answerRef = useRef("");

  useEffect(() => {
    const controller = new AbortController();
    setTrace([]); setAnswer(""); setFinal(null); setDone(null); setError(null); setQuick(null); answerRef.current = "";
    askStream(question, (e) => {
      switch (e.type) {
        case "thought": setTrace((t) => [...t, { kind: "thought", text: e.text, system: e.system }]); break;
        case "tool_call": setTrace((t) => [...t, { kind: "call", tool: e.tool, args: e.args, n: e.call }]); break;
        case "tool_result": setTrace((t) => [...t, { kind: "result", tool: e.tool, sources: e.sources, route: e.route, n: e.call }]); break;
        case "quick_answer": setQuick(e); break;
        case "token": answerRef.current += e.text; setAnswer(answerRef.current); break;
        case "answer": setFinal(e); setAnswer(e.text); break;
        case "done": setDone(e); setShowTrace(false); break;
        case "error": setError(e.message); onError(e.message); break;
        default: break;
      }
    }, controller.signal).catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); });
    return () => controller.abort();
  }, [question, onError]);

  const thinking = !done && !error;
  return (
    <div className={`${compact ? "" : "panel p-4"} flex flex-col gap-3`}>
      <div className="flex items-center gap-2 text-[12px] text-ink/60">
        <span className={`material-symbols-outlined icon-sm text-accent ${thinking ? "" : ""}`}>forum</span>
        {thinking ? (answer ? "Answering…" : "Thinking…") : error ? "Something went wrong" : final?.grounded ? "Answered from your files" : "Not found in your files"}
        {done && <span className="ml-auto text-ink/60">{done.tool_calls} search{done.tool_calls === 1 ? "" : "es"} in <span className="mono">{done.seconds} s</span>, written on this computer</span>}
      </div>

      {/* Improvement 3: a verbatim passage from the top file while the model
          works. Retrieval, not the model: shown as a quotation with its
          file, and gone as soon as the checked answer starts. */}
      {quick && !answer && !final && !error && (
        <figure className="border-l-2 border-rule pl-3">
          <blockquote className="text-[14px] leading-relaxed text-ink/80">{quick.text}</blockquote>
          <figcaption className="mt-1 text-[12px] text-ink/60">
            Closest passage in <span className="text-ink/70">{quick.source.filename}</span>. The checked answer follows.
          </figcaption>
        </figure>
      )}

      {(answer || final) && (
        <div className="rounded-lg bg-ink/[0.03] px-4 py-3 text-[15px] leading-relaxed text-ink/90">
          <Cited text={answer} sources={final?.citations ?? []} />
          {thinking && <span className="ml-0.5 inline-block h-4 w-[2px] bg-accent align-middle" />}
        </div>
      )}
      {final?.warnings?.map((w, i) => (
        <div key={i} className="flex items-start gap-2 rounded-md bg-amber-soft px-3 py-2 text-[13px] text-amber">
          <span className="material-symbols-outlined icon-sm">warning</span>{w}
        </div>
      ))}
      {error && <div className="rounded-lg bg-error/10 px-4 py-3 text-[13.5px] text-error">{error}</div>}

      {final && final.citations.length > 0 && (
        <div className="space-y-1.5">
          {final.citations.map((c) => (
            <div key={c.file_id} className="group flex items-center gap-3 rounded-lg bg-ink/[0.03] px-3 py-2" title={c.snippet}>
              <span className="mono w-6 shrink-0 text-center text-[12px] text-accent">[{c.number}]</span>
              <FileBadge filename={c.filename} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] font-medium text-ink/90">{c.filename}{c.page ? <span className="ml-2 text-[12px] text-ink/60">page {c.page}</span> : null}</div>
                <div className="mono truncate text-[12px] text-ink/60">{shortenPath(c.path)}</div>
              </div>
              {!compact && (
                <>
                  <button className="kbd opacity-0 group-hover:opacity-100" onClick={() => openResult(c.path, onError, c.file_id)}>Open</button>
                  <button className="kbd opacity-0 group-hover:opacity-100" onClick={() => revealResult(c.path, onError, c.file_id)}>{FILE_MANAGER}</button>
                </>
              )}
            </div>
          ))}
          {final.citations_inferred && <div className="text-[12px] text-ink/60">IntelliFile matched the answer to the files it came from.</div>}
        </div>
      )}

      {trace.length > 0 && (
        <div>
          <button className="flex items-center gap-1 text-[12px] text-ink/60 hover:text-ink/80" onClick={() => setShowTrace((v) => !v)}>
            <span className="material-symbols-outlined icon-sm">{showTrace ? "expand_more" : "chevron_right"}</span>
            How it got there ({trace.filter((t) => t.kind === "call").length} search{trace.filter((t) => t.kind === "call").length === 1 ? "" : "es"})
          </button>
          {showTrace && (
            <div className="mt-2 space-y-1 border-l border-rule pl-3">
              {trace.map((t, i) => {
                if (t.kind === "thought") return <div key={i} className={`text-[12.5px] ${t.system ? "text-ink/60 italic" : "text-ink/70"}`}>{t.text}</div>;
                if (t.kind === "call") return (
                  <div key={i} className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
                    <span className="chip chip-accent">{t.tool}</span>
                    {t.tool === "search" ? (<><span className="text-ink/90">“{String(t.args.query)}”</span><span className="chip">{String(t.args.mode)}</span>{t.args.filters ? <span className="chip">{String(t.args.filters)}</span> : null}</>) : <span className="text-ink/80">source [{String(t.args.source)}]</span>}
                  </div>
                );
                return (
                  <div key={i} className="text-[12px] text-ink/60">
                    Found {t.sources.length === 0 ? "nothing" : t.sources.map((s) => `[${s.number}] ${s.filename}`).join(", ")}
                    {t.route && <span className="ml-2 text-ink/60">(by {t.route.tier}, <span className="mono">{t.route.total_ms.toFixed(0)} ms</span>)</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Renders [n] markers as accent chips. */
function Cited({ text, sources }: { text: string; sources: AskSource[] }) {
  const parts = text.split(/(\[\d{1,2}\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d{1,2})\]$/);
        if (!m) return <span key={i}>{part}</span>;
        const src = sources.find((s) => s.number === Number(m[1]));
        return <span key={i} className="mono mx-0.5 rounded bg-accent/15 px-1 text-[12px] text-accent" title={src ? src.filename : undefined}>{m[1]}</span>;
      })}
    </>
  );
}

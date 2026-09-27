import { useEffect, useRef, useState } from "react";
import { AskEvent, AskSource, askStream, RouteReport } from "../backend";
import { FILE_MANAGER } from "../platform";
import { FileIcon, shortPlace } from "./kit";
import { openResult, revealResult } from "./ResultCard";

// "Searched by" names, shared with the Search page's route line.
const TIER_LABEL: Record<string, string> = {
  filename: "file name",
  metadata: "file details",
  keyword: "keywords",
  hybrid: "keywords + meaning",
  "hybrid+rerank": "keywords + meaning, reranked",
};
const MODE_LABEL: Record<string, string> = { auto: "Automatic", smart: "Meaning", keyword: "Keywords", exact: "Exact phrase" };

// The code's own checks report problems as notes; these are the ones that are checks (docs/UI_DESIGN.md section 6).
const CHECK_NOTES = [/^ignored filters/, /^dropped a citation/, /could not be traced/, /don't support the question's premise/, /^figure\(s\)/];

type Step =
  | { kind: "search"; call: number; firstLook: boolean; query: string; mode: string; filters: string; route?: RouteReport; found?: number }
  | { kind: "read"; call: number; source: string; found?: AskSource }
  | { kind: "note"; text: string };

interface Props {
  question: string;
  onError: (m: string | null) => void;
}

/**
 * One question and its answer: the closest passage at once, then the
 * answer with its sources, the evidence, what was searched, and the checks
 * the code made. The model's own reasoning text is never shown.
 */
export function AskAnswer({ question, onError }: Props) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [answer, setAnswer] = useState("");
  const [final, setFinal] = useState<Extract<AskEvent, { type: "answer" }> | null>(null);
  const [done, setDone] = useState<Extract<AskEvent, { type: "done" }> | null>(null);
  const [quick, setQuick] = useState<Extract<AskEvent, { type: "quick_answer" }> | null>(null);
  const [checkNotes, setCheckNotes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const answerRef = useRef("");

  useEffect(() => {
    const controller = new AbortController();
    setSteps([]); setAnswer(""); setFinal(null); setDone(null); setQuick(null); setCheckNotes([]); setError(null); answerRef.current = "";
    askStream(question, (e) => {
      switch (e.type) {
        case "tool_call":
          if (e.tool === "search") {
            const a = e.args as { query?: string; mode?: string; filters?: string };
            setSteps((s) => [...s, { kind: "search", call: e.call, firstLook: Boolean((e as { first_look?: boolean }).first_look), query: String(a.query ?? ""), mode: String(a.mode ?? "auto"), filters: String(a.filters ?? "") }]);
          } else {
            setSteps((s) => [...s, { kind: "read", call: e.call, source: String((e.args as { source?: unknown }).source ?? "") }]);
          }
          break;
        case "tool_result":
          setSteps((s) => s.map((st) => (st.kind === "search" && st.call === e.call ? { ...st, route: e.route, found: e.sources.length } : st.kind === "read" && st.call === e.call ? { ...st, found: e.sources[0] } : st)));
          break;
        case "thought":
          if (!e.system) break; // the model's reasoning stays out of the interface
          if (CHECK_NOTES.some((re) => re.test(e.text))) setCheckNotes((c) => [...c, e.text]);
          else setSteps((s) => (s.length && s[s.length - 1].kind === "note" && (s[s.length - 1] as { text: string }).text === e.text ? s : [...s, { kind: "note", text: e.text }]));
          break;
        case "quick_answer": setQuick(e); break;
        case "token": answerRef.current += e.text; setAnswer(answerRef.current); break;
        case "answer": setFinal(e); setAnswer(e.text); break;
        case "done": setDone(e); break;
        case "error": setError(e.message); break;
        default: break;
      }
    }, controller.signal).catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); });
    return () => controller.abort();
  }, [question]);

  const working = !done && !error;
  const citations = final?.citations ?? [];
  const usedFilters = steps.some((s) => s.kind === "search" && s.filters);
  const passed: string[] = [];
  if (final?.grounded) passed.push("Citations support the answer");
  if (final && !final.warnings?.length && final.grounded) passed.push("All figures found in the cited sources");
  if (final && usedFilters && !checkNotes.some((n) => n.startsWith("ignored filters"))) passed.push("Filters respected");

  return (
    <div className="flex flex-col gap-4">
      <div className="ml-auto max-w-[80%] rounded-lg border border-rule bg-content px-4 py-2.5 text-[14px]">{question}</div>

      <div className="panel p-5">
        <div className="flex items-center gap-2 text-[13px]">
          <span className="font-semibold">IntelliFile</span>
          <span className="text-ink/60">
            {error ? "Could not answer" : working ? (answer ? "Writing the answer..." : "Searching your files...") : final?.grounded ? "Answered from your files" : "Not found in your files"}
          </span>
          {done && <span className="ml-auto text-[12px] text-ink/60">{done.tool_calls} tool call{done.tool_calls === 1 ? "" : "s"}, <span className="mono">{done.seconds} s</span>, on this computer</span>}
        </div>

        {working && !answer && <div className="progress-indeterminate mt-3" role="progressbar" aria-label="Searching your files" />}

        {quick && !answer && !error && (
          <figure className="mt-3 border-l-2 border-rule-strong pl-3">
            <blockquote className="text-[14px] leading-relaxed text-ink/85">{quick.text}</blockquote>
            <figcaption className="mt-1 text-[12px] text-ink/60">Closest passage, in {quick.source.filename}{quick.source.page ? `, page ${quick.source.page}` : ""}. The checked answer follows.</figcaption>
          </figure>
        )}

        {answer && (
          <p className="mt-3 whitespace-pre-line text-[15px] leading-[1.65] select-text">
            <Cited text={answer} sources={citations} />
          </p>
        )}
        {final?.warnings?.map((w, i) => (
          <div key={i} className="mt-3 flex items-start gap-2 rounded-md border border-rule-strong bg-amber-soft px-3 py-2 text-[13px] text-[rgb(var(--c-amber-text))]">
            <span className="material-symbols-outlined icon-sm" aria-hidden>warning</span>{w}
          </div>
        ))}
        {error && (
          <div role="alert" className="mt-3 rounded-md border border-error/40 bg-error-soft px-3 py-2 text-[13px] text-error">
            {error} Your question is kept: ask again to retry.
            <button className="btn-ghost ml-2 px-2 py-0.5 text-[12px]" onClick={() => onError(null)}>Dismiss</button>
          </div>
        )}

        {citations.length > 0 && (
          <div className="mt-4">
            <h3 className="text-[13px] font-semibold">Sources</h3>
            <ul className="mt-1.5 overflow-hidden rounded-lg border border-rule">
              {citations.map((c) => (
                <li key={c.file_id} className="flex items-center gap-3 border-b border-rule px-3 py-2 last:border-b-0">
                  <span className="mono w-5 text-center text-[12px] text-ink/70">{c.number}</span>
                  <FileIcon filename={c.filename} size={28} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{c.filename}</span>
                    <span className="block truncate text-[12px] text-ink/60">{shortPlace(c.path)}</span>
                  </span>
                  {c.page ? <span className="shrink-0 text-[12px] text-ink/65">Page {c.page}</span> : null}
                  <button className="btn-secondary shrink-0 px-2 py-0.5 text-[12px]" onClick={() => openResult(c.path, onError, c.file_id)}>Open</button>
                  <button className="btn-ghost shrink-0 px-2 py-0.5 text-[12px]" onClick={() => revealResult(c.path, onError, c.file_id)} aria-label={`Show ${c.filename} in ${FILE_MANAGER}`}>Show</button>
                </li>
              ))}
            </ul>
            <button className="mt-3 flex items-center gap-1 text-[13px] text-ink/70 hover:text-ink" aria-expanded={showEvidence} onClick={() => setShowEvidence((v) => !v)}>
              <span className="material-symbols-outlined icon-sm" aria-hidden>{showEvidence ? "expand_more" : "chevron_right"}</span>
              {showEvidence ? "Hide evidence" : "Show evidence"}
            </button>
            {showEvidence && (
              <div className="mt-2 space-y-2">
                {citations.map((c) => (
                  <figure key={c.file_id} className="rounded-md border border-rule bg-canvas px-3 py-2">
                    <blockquote className="text-[13px] leading-relaxed text-ink/85 select-text">"{c.snippet.replace(/\*\*/g, "")}"</blockquote>
                    <figcaption className="mt-1 text-[12px] text-ink/60">{c.filename}{c.page ? `, page ${c.page}` : ""}</figcaption>
                  </figure>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {steps.length > 0 && (
        <div className="grid grid-cols-2 gap-4">
          <section className="panel p-4">
            <h3 className="text-[13px] font-semibold">Search activity</h3>
            <ol className="mt-2 space-y-1.5 text-[12.5px]">
              {steps.map((s, i) => (
                <li key={i} className="text-ink/80">
                  {s.kind === "search" && (
                    <>
                      <span className="font-medium text-ink">{s.firstLook ? "First look" : "Search"}</span>{" "}
                      <span className="mono">"{s.query}"</span>
                      <span className="text-ink/60">
                        {!s.firstLook && <>, mode {MODE_LABEL[s.mode] ?? s.mode}</>}
                        {s.filters && <>, filters {s.filters}</>}
                        {s.route && <>, route {TIER_LABEL[s.route.tier] ?? s.route.tier}, <span className="mono">{s.route.total_ms.toFixed(0)} ms</span></>}
                        {s.found !== undefined && <>, {s.found} found</>}
                      </span>
                    </>
                  )}
                  {s.kind === "read" && <><span className="font-medium text-ink">Read more</span> <span className="text-ink/70">{s.found?.filename ?? `source ${s.source}`}</span></>}
                  {s.kind === "note" && <span className="text-ink/60">{s.text}</span>}
                </li>
              ))}
              {final && <li className="font-medium text-ink">Answer written</li>}
            </ol>
          </section>
          <section className="panel p-4">
            <h3 className="text-[13px] font-semibold">Checks</h3>
            {!final && !error && <p className="mt-2 text-[12.5px] text-ink/60">Made by IntelliFile's code once the answer is written.</p>}
            <ul className="mt-2 space-y-1.5 text-[12.5px]">
              {passed.map((p) => (
                <li key={p} className="flex gap-2"><span className="material-symbols-outlined icon-sm text-marker" aria-hidden>check</span><span>Passed: {p}</span></li>
              ))}
              {[...new Set(checkNotes)].map((n) => {
                const times = checkNotes.filter((x) => x === n).length;
                return <li key={n} className="flex gap-2"><span className="material-symbols-outlined icon-sm text-error" aria-hidden>close</span><span>Problem: {n}{times > 1 ? ` (${times} times)` : ""}</span></li>;
              })}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}

/** [n] markers as small numbered tags. */
function Cited({ text, sources }: { text: string; sources: AskSource[] }) {
  const parts = text.split(/(\[\d{1,2}\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d{1,2})\]$/);
        if (!m) return <span key={i}>{part}</span>;
        const src = sources.find((s) => s.number === Number(m[1]));
        return <sup key={i} className="mono mx-0.5 rounded border border-rule-strong px-1 text-[11px] text-ink/80" title={src?.filename}>{m[1]}</sup>;
      })}
    </>
  );
}

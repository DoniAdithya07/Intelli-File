import { KeyboardEvent, useCallback, useEffect, useState } from "react";
import { askStatus, getProfile, listEvents } from "../backend";
import { useVoice } from "../hooks/useVoice";
import { AskAnswer } from "../ui/AskAnswer";
import { PageHeader } from "../ui/kit";

interface Props {
  question: string | null; // handed over from Search ("?" or "Ask instead")
  onError: (m: string | null) => void;
}

/**
 * Suggested questions, built only from real activity (docs/UI_DESIGN.md
 * section 7): the topics learned from opened files and files opened lately.
 * With no activity there are none, rather than invented ones.
 */
async function suggestions(): Promise<string[]> {
  const out: string[] = [];
  try {
    const profile = await getProfile();
    for (const t of profile.topics.slice(0, 2)) if (t.terms[0]) out.push(`What do my files say about ${t.terms[0]}?`);
  } catch { /* none */ }
  try {
    const events = await listEvents(100);
    const names: string[] = [];
    for (const e of events.events) {
      const name = e.kind === "file_opened" && e.path ? e.path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/, "") : null;
      if (name && !names.includes(name)) names.push(name);
    }
    for (const n of names.slice(0, 4 - out.length)) out.push(`What is in ${n}?`);
  } catch { /* none */ }
  return out.slice(0, 4);
}

export function AskPage({ question, onError }: Props) {
  const [draft, setDraft] = useState("");
  const [asked, setAsked] = useState<string | null>(question);
  const [session, setSession] = useState<string[]>(question ? [question] : []);
  const [suggested, setSuggested] = useState<string[]>([]);
  const [model, setModel] = useState<boolean | null>(null);

  useEffect(() => { suggestions().then(setSuggested); askStatus().then((s) => setModel(s.available)).catch(() => setModel(null)); }, []);
  useEffect(() => { if (question) { setAsked(question); setSession((s) => [question, ...s.filter((q) => q !== question)]); } }, [question]);

  const submit = (text?: string) => {
    const q = (text ?? draft).trim().replace(/^\?/, "").trim();
    if (!q) return;
    setAsked(q);
    setSession((s) => [q, ...s.filter((x) => x !== q)]);
    setDraft("");
  };
  const voice = useVoice(useCallback((t: string) => setDraft(t), []), useCallback((m: string) => onError(m), [onError]));
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } };
  const listening = voice.state === "recording";

  const box = (
    <div className="omnibox rounded-lg border border-rule-strong bg-content focus-within:border-accent">
      <label htmlFor="ask-input" className="sr-only">Your question</label>
      <textarea
        id="ask-input"
        className="block min-h-[72px] w-full resize-none bg-transparent px-4 pt-3 text-[15px] outline-none placeholder:text-ink/50"
        placeholder={listening ? "Listening. Click the microphone again to stop." : "Ask a question about your files"}
        value={draft}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onKeyDown={onKey}
        autoFocus
        spellCheck={false}
      />
      <div className="flex items-center justify-between px-3 pb-3">
        <span className="text-[12px] text-ink/55">Enter to ask, Shift+Enter for a new line</span>
        <div className="flex gap-2">
          <button className={`grid h-9 w-9 place-items-center rounded-md ${listening ? "mic-rec" : "btn-secondary"}`} onClick={voice.toggle} aria-label={listening ? "Stop recording" : "Ask by voice"} title={listening ? "Stop recording" : "Ask by voice"}>
            <span className="material-symbols-outlined icon-sm">{listening ? "stop" : "mic"}</span>
          </button>
          <button className="btn-primary grid h-9 w-9 place-items-center" onClick={() => submit()} disabled={!draft.trim()} aria-label="Ask" title="Ask">
            <span className="material-symbols-outlined icon-sm">arrow_forward</span>
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex h-full gap-5">
      <div className="min-w-0 flex-1 overflow-y-auto pr-1">
        {!asked ? (
          <div className="mx-auto max-w-[760px] space-y-6 pt-2">
            <PageHeader title="Ask IntelliFile" subtitle="Ask questions about your files. Everything stays on this computer." />
            {model === false && (
              <div role="alert" className="rounded-md border border-rule-strong bg-content px-4 py-3 text-[13px]">
                <b>Ask needs its language model.</b> The <span className="mono">models\llm</span> folder inside the IntelliFile folder is missing. Extract IntelliFile-windows.zip again, completely, then ask again. Search works without it.
              </div>
            )}
            {box}
            {suggested.length > 0 && (
              <section>
                <h2 className="text-[15px] font-semibold">Suggested questions</h2>
                <p className="text-[12px] text-ink/60">From the topics and files you worked with lately.</p>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {suggested.map((q) => (
                    <button key={q} className="btn-secondary flex items-center gap-2 px-3 py-2.5 text-left text-[13px]" onClick={() => submit(q)}>
                      <span className="material-symbols-outlined icon-sm text-ink/60" aria-hidden>chat_bubble</span>
                      <span className="truncate">{q}</span>
                    </button>
                  ))}
                </div>
              </section>
            )}
            <section className="grid grid-cols-2 gap-4 text-[13px]">
              <div>
                <h2 className="font-semibold">What Ask can do</h2>
                <p className="mt-1 text-ink/70">Find the passages that answer a question and write a short answer that names its sources.</p>
              </div>
              <div>
                <h2 className="font-semibold">What it cannot do</h2>
                <p className="mt-1 text-ink/70">Answer from anything outside your indexed folders, remember earlier questions, or change a file.</p>
              </div>
            </section>
          </div>
        ) : (
          <div className="mx-auto max-w-[860px] space-y-4">
            <AskAnswer key={asked} question={asked} onError={onError} />
            {box}
          </div>
        )}
      </div>

      {session.length > 0 && (
        <aside aria-label="Questions this session" className="w-[220px] shrink-0 overflow-y-auto border-l border-rule pl-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[13px] font-semibold">This session</h2>
            <button className="text-[12px] text-ink/65 hover:text-ink hover:underline" onClick={() => setAsked(null)}>New question</button>
          </div>
          <ul className="mt-2 space-y-1">
            {session.map((q) => (
              <li key={q}>
                <button className={`w-full rounded-md px-2 py-1.5 text-left text-[13px] ${q === asked ? "bg-content font-medium" : "text-ink/75 hover:bg-ink/[0.05]"}`} onClick={() => setAsked(q)}>
                  <span className="line-clamp-2">{q}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12px] text-ink/55">Each question is answered on its own.</p>
        </aside>
      )}
    </div>
  );
}

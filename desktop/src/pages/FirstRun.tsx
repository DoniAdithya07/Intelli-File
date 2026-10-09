import { useState } from "react";
import { AccessMode } from "../backend";

type Choice = Exclude<AccessMode, "unset">;

// docs/UI_DESIGN.md section 14. "Nothing yet" is the backend's "denied" mode.
const CHOICES: { id: Choice; title: string; body: string; note?: string }[] = [
  {
    id: "all",
    title: "Whole computer",
    body: "Your folders and drives, system folders skipped.",
    note: "Indexing everything can take a long time, and Ask is slower until it finishes.",
  },
  { id: "limited", title: "Only folders I choose", body: "Add folders or single files on the Index page. Nothing else is read." },
  { id: "denied", title: "Nothing yet", body: "You can choose later in Settings." },
];

interface Props {
  busy: boolean;
  onChoose: (mode: Choice) => void;
  /** The shipped sample folder, when there is one. */
  samplePath: string | null;
  onTrySample: () => void;
}

/** The first-run question: nothing is read until it is answered. */
export function FirstRun({ busy, onChoose, samplePath, onTrySample }: Props) {
  const [choice, setChoice] = useState<Choice>("limited");
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto">
      <div className="panel anim-fade w-full max-w-[560px] p-8">
        <h1 className="text-[26px] font-semibold leading-tight">Welcome to IntelliFile</h1>
        <p className="mt-1 text-[14px] text-ink/70">Search and ask about your files. Nothing leaves this computer.</p>

        <fieldset className="mt-6">
          <legend className="text-[15px] font-semibold">What may IntelliFile read?</legend>
          <div className="mt-3 space-y-2">
            {CHOICES.map((c) => (
              <label key={c.id} className={`flex cursor-pointer gap-3 rounded-md border px-4 py-3 ${choice === c.id ? "border-accent bg-accent-soft" : "border-rule hover:bg-ink/[0.03]"}`}>
                <input type="radio" name="access" className="mt-1 accent-[rgb(var(--c-accent))]" checked={choice === c.id} onChange={() => setChoice(c.id)} />
                <span>
                  <span className="block text-[14px] font-medium">{c.title}</span>
                  <span className="block text-[13px] text-ink/70">{c.body}</span>
                  {c.note && <span className="mt-0.5 block text-[12px] text-[rgb(var(--c-amber-text))]">{c.note}</span>}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="mt-6 flex flex-wrap gap-2">
          <button className="btn-primary px-4 py-2 text-[14px]" onClick={() => onChoose(choice)} disabled={busy}>Continue</button>
          {samplePath && (
            <button className="btn-secondary px-4 py-2 text-[14px]" onClick={onTrySample} disabled={busy} title={samplePath}>Try the sample folder</button>
          )}
        </div>
        <p className="mt-4 text-[12px] text-ink/65">IntelliFile only reads your files. It never changes, moves or deletes them.</p>
      </div>
    </div>
  );
}

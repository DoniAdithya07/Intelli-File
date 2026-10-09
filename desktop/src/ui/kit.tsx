import { ReactNode } from "react";
import { fileKind, FileGroup } from "../backend";

/** The title block every page opens with. */
export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold leading-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-[14px] text-ink/65">{subtitle}</p>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
}

/** A titled block of a page. `action` is a quiet link on the right, such as "See all". */
export function Section({ title, note, action, children, className = "" }: { title: string; note?: ReactNode; action?: { label: string; onClick: () => void }; children: ReactNode; className?: string }) {
  return (
    <section className={`panel p-4 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {action && <button className="text-[13px] text-ink/65 underline-offset-2 hover:text-ink hover:underline" onClick={action.onClick}>{action.label}</button>}
      </div>
      {note && <p className="mt-0.5 text-[12px] text-ink/65">{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

const GROUP_ICON: Record<FileGroup, string> = {
  text: "description",
  doc: "article",
  sheet: "table_chart",
  slides: "slideshow",
  code: "code",
  image: "image",
  video: "movie",
  audio: "graphic_eq",
};

/** A file's kind as a line icon in a small square, with its extension underneath for the eye that wants it. */
export function FileIcon({ filename, size = 36 }: { filename: string; size?: number }) {
  const k = fileKind(filename);
  // PDF gets its own colour; the other documents share one.
  const tone = k.badge === "PDF" ? "pdf" : k.group;
  return (
    <span
      className="ft grid shrink-0 place-items-center rounded-md"
      style={{ width: size, height: size, ["--ft" as string]: `var(--ft-${tone})` }}
      title={k.badge}
      aria-hidden
    >
      <span className="flex flex-col items-center leading-none">
        <span className="material-symbols-outlined" style={{ fontSize: size * 0.46 }}>{GROUP_ICON[k.group]}</span>
        {size >= 32 && <span className="mono mt-0.5 text-[10px] font-semibold">{k.badge.slice(0, 4)}</span>}
      </span>
    </span>
  );
}

/** An on/off switch: a rectangle with a square knob, never a pill. */
export function Toggle({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`toggle relative h-[22px] w-10 shrink-0 rounded-[5px] border ${on ? "border-accent bg-accent" : "border-rule-strong bg-ink/10"} ${disabled ? "opacity-50" : ""}`}
    >
      <span className={`toggle-knob absolute top-[2px] h-4 w-4 rounded-[3px] ${on ? "left-[20px] bg-[rgb(var(--c-on-accent))]" : "left-[2px] bg-content"}`} />
    </button>
  );
}

/** Underlined tabs, as on the Search and Photos pages. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-4">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          className={`-mb-px border-b-2 pb-2 text-[14px] ${value === t.id ? "border-accent font-medium text-ink" : "border-transparent text-ink/65 hover:text-ink"}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** "Documents\Research": the last two folders of a path, which is how people recognise a place. */
export function shortPlace(path: string | null): string {
  if (!path) return "";
  const parts = path.split(/[\\/]/).filter(Boolean);
  parts.pop();
  return parts.slice(-2).join("\\");
}

/**
 * In place of a section whose data the engine refused (it answered with an error).
 * When the engine cannot be reached at all the sidebar already says so, and this is not shown.
 */
export function LoadFailed({ what = "this section" }: { what?: string }) {
  return (
    <p role="status" className="flex items-center gap-1.5 text-[12px] text-ink/70">
      <span className="material-symbols-outlined icon-sm" aria-hidden>info</span>
      Could not load {what}.
    </p>
  );
}

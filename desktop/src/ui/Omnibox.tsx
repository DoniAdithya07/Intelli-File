import { KeyboardEvent, MutableRefObject, useEffect, useRef } from "react";
import type { VoiceState } from "../hooks/useVoice";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onEscape?: () => void;
  onArrow?: (dir: 1 | -1) => void;
  /** The results list this box drives and its highlighted row, so screen readers follow the arrow keys. */
  listId?: string;
  activeId?: string;
  placeholder: string;
  icon?: string;
  searching?: boolean;
  voice?: { state: VoiceState; level: number; toggle: () => void };
  autoFocus?: boolean;
  trailing?: React.ReactNode;
  large?: boolean;
  /** One line high, for the address row. */
  compact?: boolean;
  /** Lets the page focus the box (Ctrl+F). */
  inputRef?: MutableRefObject<HTMLInputElement | null>;
}

/** The search bar, shared by Search, Photos and the Ctrl+Space overlay. */
export function Omnibox({ value, onChange, onSubmit, onEscape, onArrow, listId, activeId, placeholder, icon = "search", searching, voice, autoFocus, trailing, large, compact, inputRef: outerRef }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);
  useEffect(() => { if (outerRef) outerRef.current = inputRef.current; });

  const v = voice?.state ?? "idle";
  const busy = v === "warming" || v === "recording" || v === "transcribing";

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    // Ctrl+Enter belongs to the page ("show in Explorer"); it must not also search or open.
    if (e.key === "Enter") { e.preventDefault(); if (!(e.ctrlKey || e.metaKey)) onSubmit(); }
    else if (e.key === "Escape") { e.preventDefault(); if (value) onChange(""); else onEscape?.(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); onArrow?.(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); onArrow?.(-1); }
  }

  // While recording, the bars show the real microphone level (voice.level), not a loop.
  const levelBars = (
    <span className="bars" aria-hidden>
      {[...Array(7)].map((_, i) => (
        <i key={i} style={{ height: `${Math.max(4, Math.min(16, 4 + (voice?.level ?? 0) * 60 * ((i % 3) + 1) / 2))}px` }} />
      ))}
    </span>
  );

  return (
    <div
      aria-busy={searching || undefined}
      className={`omnibox no-drag flex items-center border bg-field ${compact ? "h-8 gap-2 px-2" : `gap-3 rounded-lg px-4 ${large ? "h-14" : "h-12"}`} ${
        v === "recording" ? "border-error" : "border-rule-strong focus-within:border-accent"
      }`}
    >
      {v === "recording" ? levelBars : (
        <span className={`material-symbols-outlined text-ink/65 ${compact ? "icon-sm" : ""}`}>{v === "warming" || v === "transcribing" ? "graphic_eq" : icon}</span>
      )}
      <input
        ref={inputRef}
        aria-label={placeholder}
        {...(listId ? { role: "combobox", "aria-expanded": true, "aria-controls": listId, "aria-activedescendant": activeId, "aria-autocomplete": "none" as const } : {})}
        className={`min-w-0 flex-1 bg-transparent outline-none placeholder:text-dim ${compact ? "text-[14px]" : large ? "text-[17px]" : "text-[15px]"}`}
        value={busy ? "" : value}
        placeholder={v === "warming" ? "Getting the microphone ready…" : v === "recording" ? "Listening. Click the microphone again to stop." : v === "transcribing" ? "Turning your words into text…" : placeholder}
        onChange={(e) => onChange(e.currentTarget.value)}
        onKeyDown={onKey}
        disabled={busy}
        spellCheck={false}
      />
      {value && !busy && (
        <button className="btn-ghost p-1" onClick={() => { onChange(""); inputRef.current?.focus(); }} title="Clear" aria-label="Clear">
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      )}
      {trailing}
      {voice && (
        <button
          className={`grid ${compact ? "h-6 w-6" : "h-9 w-9"} shrink-0 place-items-center rounded-md transition-colors ${v === "recording" ? "mic-rec" : compact ? "btn-ghost text-accent" : "btn-secondary text-accent"} disabled:opacity-50`}
          onClick={voice.toggle}
          disabled={v === "warming" || v === "transcribing"}
          title={v === "recording" ? "Stop recording" : "Search by voice"}
          aria-label={v === "recording" ? "Stop recording" : "Search by voice"}
        >
          <span className={`material-symbols-outlined ${compact ? "icon-sm" : ""}`}>{v === "recording" ? "stop" : "mic"}</span>
        </button>
      )}
    </div>
  );
}

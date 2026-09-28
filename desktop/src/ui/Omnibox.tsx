import { KeyboardEvent, useEffect, useRef } from "react";
import type { VoiceState } from "../hooks/useVoice";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onEscape?: () => void;
  onArrow?: (dir: 1 | -1) => void;
  placeholder: string;
  icon?: string;
  searching?: boolean;
  voice?: { state: VoiceState; level: number; toggle: () => void };
  autoFocus?: boolean;
  trailing?: React.ReactNode;
  large?: boolean;
}

/** The search bar, shared by Search, Photos and the Ctrl+Space overlay. */
export function Omnibox({ value, onChange, onSubmit, onEscape, onArrow, placeholder, icon = "search", searching, voice, autoFocus, trailing, large }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

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
      className={`omnibox no-drag flex items-center gap-3 rounded-lg border bg-content px-4 ${large ? "h-14" : "h-12"} ${
        v === "recording" ? "border-error" : "border-rule-strong focus-within:border-accent"
      }`}
    >
      {v === "recording" ? levelBars : (
        <span className="material-symbols-outlined text-ink/60">{v === "warming" || v === "transcribing" ? "graphic_eq" : icon}</span>
      )}
      <input
        ref={inputRef}
        aria-label={placeholder}
        className={`flex-1 bg-transparent outline-none placeholder:text-ink/55 ${large ? "text-[17px]" : "text-[15px]"}`}
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
          className={`grid h-9 w-9 place-items-center rounded-md transition-colors ${v === "recording" ? "mic-rec" : "btn-secondary text-accent"} disabled:opacity-50`}
          onClick={voice.toggle}
          disabled={v === "warming" || v === "transcribing"}
          title={v === "recording" ? "Stop recording" : "Search by voice"}
          aria-label={v === "recording" ? "Stop recording" : "Search by voice"}
        >
          <span className="material-symbols-outlined">{v === "recording" ? "stop" : "mic"}</span>
        </button>
      )}
    </div>
  );
}

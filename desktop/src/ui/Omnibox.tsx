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

/** The search bar — shared by Search, Photos and the Ctrl+Space overlay. */
export function Omnibox({ value, onChange, onSubmit, onEscape, onArrow, placeholder, icon = "search", searching, voice, autoFocus, trailing, large }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const v = voice?.state ?? "idle";
  const busy = v === "warming" || v === "recording" || v === "transcribing";

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") { e.preventDefault(); onSubmit(); }
    else if (e.key === "Escape") { e.preventDefault(); if (value) onChange(""); else onEscape?.(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); onArrow?.(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); onArrow?.(-1); }
  }

  const bars = (warm: boolean) => (
    <span className={`bars ${warm ? "warm" : ""}`} aria-hidden>
      {[...Array(7)].map((_, i) => (
        <i key={i} style={!warm ? { height: `${Math.max(4, Math.min(16, 4 + (voice?.level ?? 0) * 60 * ((i % 3) + 1) / 2))}px`, animation: "none" } : undefined} />
      ))}
    </span>
  );

  return (
    <div
      className={`no-drag flex items-center gap-3 rounded-xl border px-4 ${large ? "h-14" : "h-12"} ${searching ? "sweep" : ""} ${v === "recording" ? "border-[#ff5c6a]" : "border-white/[0.07]"} bg-white/[0.03] focus-within:shadow-glow transition-shadow`}
      style={{ borderColor: v === "recording" ? "#ff5c6a" : undefined }}
    >
      {v === "warming" ? bars(true) : v === "recording" ? bars(false) : v === "transcribing" ? (
        <span className="dots"><span /><span /><span /></span>
      ) : (
        <span className="material-symbols-outlined text-white/60">{icon}</span>
      )}
      <input
        ref={inputRef}
        className={`flex-1 bg-transparent outline-none placeholder:text-white/35 ${large ? "text-[17px]" : "text-[15px]"}`}
        value={busy ? "" : value}
        placeholder={v === "warming" ? "getting ready…" : v === "recording" ? "listening… click the mic to stop" : v === "transcribing" ? "transcribing…" : placeholder}
        onChange={(e) => onChange(e.currentTarget.value)}
        onKeyDown={onKey}
        disabled={busy}
        spellCheck={false}
      />
      {value && !busy && (
        <button className="btn-ghost p-1" onClick={() => { onChange(""); inputRef.current?.focus(); }} title="Clear">
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      )}
      {trailing}
      {voice && (
        <button
          className={`grid h-9 w-9 place-items-center rounded-lg transition-colors ${v === "recording" ? "mic-rec bg-[#d63c4a] text-white" : "btn-gradient text-accent"} disabled:opacity-50`}
          onClick={voice.toggle}
          disabled={v === "warming" || v === "transcribing"}
          title={v === "recording" ? "Stop recording" : "Search by voice"}
        >
          <span className="material-symbols-outlined">{v === "recording" ? "stop" : "mic"}</span>
        </button>
      )}
    </div>
  );
}

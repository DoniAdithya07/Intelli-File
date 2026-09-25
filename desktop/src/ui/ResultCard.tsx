import { useState } from "react";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { fileKind, formatAgo, formatBytes, recordEvent, shortenPath, SearchResult } from "../backend";
import { FILE_MANAGER, MOD_KEY } from "../platform";

// FTS5's snippet() wraps matches in **double asterisks** — render them as highlights.
export function Highlighted({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? <mark className="hl" key={i}>{part.slice(2, -2)}</mark> : <span key={i}>{part}</span>
      )}
    </>
  );
}

// Opening and revealing are the two strongest signals of what a file
// means to the user (Phase 16) — reported whichever page they come from.
export async function openResult(path: string | null, onError: (m: string) => void, fileId?: string | null) {
  if (!path) return;
  try {
    await openPath(path);
    recordEvent("file_opened", { file_id: fileId ?? null, path });
  } catch (e) { onError(e instanceof Error ? e.message : String(e)); }
}
export async function revealResult(path: string | null, onError: (m: string) => void, fileId?: string | null) {
  if (!path) return;
  try {
    await revealItemInDir(path);
    recordEvent("file_revealed", { file_id: fileId ?? null, path });
  } catch (e) { onError(e instanceof Error ? e.message : String(e)); }
}

export function FileBadge({ filename }: { filename: string }) {
  const k = fileKind(filename);
  return <span className={`badge badge-${k.group}`}>{k.badge}</span>;
}

// Phase 17: reasons that come from the user's own habits, not the file.
const PERSONAL_PREFIXES = ["You use this", "You used to", "Your usual", "Your most-used"];

function whyChip(reason: string, i: number) {
  const accent = reason.startsWith("Contains") || reason.startsWith("Filename");
  const indigo = reason.startsWith("Matched based on meaning");
  const personal = PERSONAL_PREFIXES.some((p) => reason.startsWith(p));
  const label = reason.startsWith("Matched based on meaning") ? "Matched on meaning" : reason;
  const icon = accent ? "done_all" : indigo ? "psychology" : personal ? "person" : reason.startsWith("Re-read by the reranker") ? "fact_check" : reason.startsWith("Matches filters") ? "filter_alt" : reason.startsWith("Found on page") || reason.startsWith("Found on slide") ? "description" : "sell";
  return (
    <span key={i} className={`chip inline-flex items-center gap-1 ${accent ? "chip-accent" : indigo ? "chip-indigo" : personal ? "chip-personal" : ""}`}>
      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{icon}</span>
      {label}
    </span>
  );
}

interface Props {
  result: SearchResult;
  selected: boolean;
  compact?: boolean;
  onSelect: () => void;
  onError: (m: string) => void;
}

/** One search result — matches the "Top Matches" card in the Search design. */
export function ResultCard({ result, selected, compact, onSelect, onError }: Props) {
  const [hover, setHover] = useState(false);
  const isAudio = fileKind(result.filename).group === "audio";
  const weak = result.confidence === "weak";
  return (
    <div
      className={`panel relative cursor-default px-4 py-3 transition-colors ${selected ? "border-white/[0.12] bg-[color:var(--selected)]" : "hover:bg-white/[0.03]"} ${weak ? "opacity-60" : ""}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => { onSelect(); recordEvent("result_clicked", { file_id: result.file_id, path: result.path }); }}
      onDoubleClick={() => openResult(result.path, onError, result.file_id)}
    >
      {selected && <span className="absolute left-0 top-3 bottom-3 w-[3px] rounded-r bg-gradient-to-b from-accent to-accent2" />}
      <div className="flex items-center gap-3">
        <FileBadge filename={result.filename} />
        <span className="truncate text-[15px] font-semibold">{result.filename}</span>
        {result.path && <span className="mono truncate text-[11px] text-white/40">{shortenPath(result.path)}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {(hover || selected) && !compact && (
            <>
              <button className="kbd hover:text-white" onClick={(e) => { e.stopPropagation(); openResult(result.path, onError, result.file_id); }}>↵ Open</button>
              <button className="kbd hover:text-white" onClick={(e) => { e.stopPropagation(); revealResult(result.path, onError, result.file_id); }}>{MOD_KEY}↵ {FILE_MANAGER}</button>
            </>
          )}
        </span>
      </div>
      {result.matched_chunk && (
        <div className={`mt-2 rounded-lg bg-white/[0.03] px-3 py-2 text-[13.5px] leading-relaxed text-white/85 ${compact ? "line-clamp-1" : "line-clamp-2"} ${isAudio ? "italic" : ""}`}>
          {isAudio ? "“" : ""}<Highlighted text={result.matched_chunk} />{isAudio ? "”" : ""}
        </div>
      )}
      {!compact && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {result.why.map(whyChip)}
          {isAudio && <span className="chip inline-flex items-center gap-1"><span className="material-symbols-outlined" style={{ fontSize: 13 }}>graphic_eq</span>Spoken audio (Whisper, local)</span>}
          <span className="mono ml-auto text-[11px] text-white/35">
            {formatBytes(result.size)} · Modified {formatAgo(result.modified_time)}
          </span>
        </div>
      )}
    </div>
  );
}

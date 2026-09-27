import { useState } from "react";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { fileKind, formatAgo, formatBytes, recordEvent, SearchResult, shortenFolder } from "../backend";
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
  const icon = accent ? "done_all" : indigo ? "notes" : personal ? "person" : reason.startsWith("Re-read by the reranker") ? "fact_check" : reason.startsWith("Matches filters") ? "filter_alt" : reason.startsWith("Found on page") || reason.startsWith("Found on slide") ? "description" : "sell";
  return (
    <span key={i} className={`chip inline-flex items-center gap-1 ${accent ? "chip-accent" : indigo ? "chip-accent" : personal ? "chip-personal" : ""}`}>
      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{icon}</span>
      {label}
    </span>
  );
}

/** The ruled list the result rows sit in, like cards in a catalogue drawer. */
export const RESULT_LIST = "overflow-hidden rounded-lg border border-rule bg-content";

interface Props {
  result: SearchResult;
  selected: boolean;
  compact?: boolean;
  onSelect: () => void;
  onError: (m: string) => void;
}

/**
 * One search result: a ruled catalogue-card row. The file-type tag is its
 * "call number"; the matched passage is quoted under the name; the reasons
 * it matched sit on the last line. Rows sit inside RESULT_LIST.
 */
export function ResultCard({ result, selected, compact, onSelect, onError }: Props) {
  const [hover, setHover] = useState(false);
  const isAudio = fileKind(result.filename).group === "audio";
  const weak = result.confidence === "weak";
  return (
    <div
      className={`relative cursor-default border-b border-rule px-4 py-3 last:border-b-0 transition-colors ${
        selected ? "bg-[color:var(--selected)] shadow-[inset_3px_0_0_var(--marker)]" : "hover:bg-ink/[0.025]"
      } ${weak ? "text-ink/80" : ""}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => { onSelect(); recordEvent("result_clicked", { file_id: result.file_id, path: result.path }); }}
      onDoubleClick={() => openResult(result.path, onError, result.file_id)}
    >
      <div className="flex items-center gap-3">
        <FileBadge filename={result.filename} />
        <span className={`min-w-0 shrink truncate text-[15px] ${weak ? "font-medium" : "font-semibold"}`}>{result.filename}</span>
        {result.path && <span className="mono min-w-0 flex-1 truncate text-[12px] text-ink/60" title={result.path}>in {shortenFolder(result.path)}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {(hover || selected) && !compact && (
            <>
              <button className="btn-secondary px-2 py-0.5 text-[12px]" onClick={(e) => { e.stopPropagation(); openResult(result.path, onError, result.file_id); }}>Open <span className="kbd ml-1">Enter</span></button>
              <button className="btn-secondary px-2 py-0.5 text-[12px]" onClick={(e) => { e.stopPropagation(); revealResult(result.path, onError, result.file_id); }}>Show in {FILE_MANAGER} <span className="kbd ml-1">{MOD_KEY}+Enter</span></button>
            </>
          )}
        </span>
      </div>
      {result.matched_chunk && (
        <div className={`mt-1.5 border-l-2 border-rule pl-3 text-[13.5px] leading-relaxed text-ink/90 ${compact ? "line-clamp-1" : "line-clamp-2"} ${isAudio ? "italic" : ""}`}>
          {isAudio ? "“" : ""}<Highlighted text={result.matched_chunk} />{isAudio ? "”" : ""}
        </div>
      )}
      {!compact && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {result.why.map(whyChip)}
          {isAudio && <span className="chip inline-flex items-center gap-1"><span className="material-symbols-outlined" style={{ fontSize: 13 }}>graphic_eq</span>Spoken audio, transcribed on this computer</span>}
          <span className="ml-auto text-[12px] text-ink/60">
            <span className="mono">{formatBytes(result.size)}</span>, modified {formatAgo(result.modified_time)}
          </span>
        </div>
      )}
    </div>
  );
}

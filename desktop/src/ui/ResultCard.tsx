import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { fileKind, recordEvent } from "../backend";

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

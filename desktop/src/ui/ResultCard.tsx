import { invoke } from "@tauri-apps/api/core";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
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

// Mirrors ALLOWED_EXTENSIONS in src-tauri/src/lib.rs: the only types the shell opens.
const OPENABLE = new Set([
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt", "md", "csv", "tsv", "json",
  "log", "epub", "eml", "msg", "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif", "mp3",
  "wav", "m4a", "flac", "ogg", "aiff", "aif", "wma", "aac", "opus", "avif", "mp4", "mov", "m4v", "avi", "mkv", "webm",
  "3gp", "wmv", "mts", "m2ts", "mpg", "mpeg",
]);
/** False for types the shell refuses to open (code, scripts, web pages): offer only "Show in Explorer" for those. */
export function canOpen(path: string | null): boolean {
  const ext = path ? /\.([^.\\/\s]+)[.\s]*$/.exec(path)?.[1] : undefined;
  return !!ext && OPENABLE.has(ext.toLowerCase());
}

// Opening and revealing are the two strongest signals of what a file
// means to the user (Phase 16) — reported whichever page they come from.
export async function openResult(path: string | null, onError: (m: string) => void, fileId?: string | null) {
  if (!path) return;
  try {
    await invoke("open_indexed_path", { path });
    recordEvent("file_opened", { file_id: fileId ?? null, path });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    // The shell refuses anything but documents, images, audio and video: say which type it refused.
    const ext = /\.([^.\/\s]+)[.\s]*$/.exec(path)?.[1];
    onError(message.includes("only opens") && ext ? `.${ext.toLowerCase()} files can't be opened from IntelliFile. It only opens documents, images, audio and video.` : message);
  }
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

import { BrowseKind } from "../backend";

/** Explorer's size style: 48 KB, 1.2 MB. */
export function sizeText(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

/** Date and time in the user's Windows format. `epoch` is seconds. */
export function dateText(epoch: number | null | undefined): string {
  if (epoch == null) return "";
  return new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

const TYPE_NAMES: Record<string, string> = {
  pdf: "PDF Document", doc: "Word Document", docx: "Word Document", rtf: "Rich Text Document", odt: "OpenDocument Text", txt: "Text Document", md: "Markdown File",
  xls: "Excel Worksheet", xlsx: "Excel Worksheet", csv: "CSV File", tsv: "TSV File", ods: "OpenDocument Spreadsheet",
  ppt: "PowerPoint Presentation", pptx: "PowerPoint Presentation", odp: "OpenDocument Presentation",
  png: "PNG Image", jpg: "JPEG Image", jpeg: "JPEG Image", gif: "GIF Image", bmp: "Bitmap Image", webp: "WebP Image", heic: "HEIC Image", tif: "TIFF Image", tiff: "TIFF Image", svg: "SVG Image",
  mp3: "MP3 Audio", wav: "WAV Audio", m4a: "M4A Audio", flac: "FLAC Audio", ogg: "OGG Audio", aac: "AAC Audio",
  mp4: "MP4 Video", mov: "QuickTime Video", mkv: "MKV Video", avi: "AVI Video", webm: "WebM Video",
  zip: "Compressed Folder", "7z": "7-Zip Archive", rar: "RAR Archive", json: "JSON File", html: "HTML File", exe: "Application", log: "Log File",
};
const KIND_NAMES: Record<BrowseKind, string> = { folder: "File folder", image: "Image", audio: "Audio", video: "Video", document: "Document", code: "Source file", archive: "Archive", other: "File" };

export function typeName(name: string, kind: BrowseKind): string {
  if (kind === "folder") return KIND_NAMES.folder;
  const ext = /\.([^.\\/]+)$/.exec(name)?.[1]?.toLowerCase();
  if (ext && TYPE_NAMES[ext]) return TYPE_NAMES[ext];
  return ext ? `${ext.toUpperCase()} File` : KIND_NAMES[kind];
}

/** Names compare the way Explorer does: "file2" before "file10". */
export const naturalCompare = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });

const TRAILING = /[\\/]+$/;
/** Windows paths compare without case and without a trailing slash. */
export const samePath = (a: string, b: string) => a.replace(TRAILING, "").toLowerCase() === b.replace(TRAILING, "").toLowerCase();
export const isInside = (path: string, root: string) => {
  const p = path.replace(TRAILING, "").toLowerCase(), r = root.replace(TRAILING, "").toLowerCase();
  return p === r || p.startsWith(r + "\\");
};
/** "C:\Users\me" gives C: (path "C:\"), Users (path "C:\Users"), me (path "C:\Users\me"). */
export function pathSegments(path: string): { name: string; path: string }[] {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.map((name, i) => ({ name, path: i === 0 ? `${name}\\` : parts.slice(0, i + 1).join("\\") }));
}
export const baseName = (path: string) => path.split(/[\\/]+/).filter(Boolean).pop() ?? path;
export const parentOf = (path: string): string | null => {
  const seg = pathSegments(path);
  return seg.length > 1 ? seg[seg.length - 2].path : null;
};

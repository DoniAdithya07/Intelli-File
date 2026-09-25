// Dev-time only: the Python backend is run manually on this port during
// development. Once the sidecar wiring lands (Phase 14), the port will be
// resolved dynamically instead of hardcoded.
export const BACKEND_URL = "http://127.0.0.1:8756";

// Phase 12: inside the desktop shell every request carries the per-launch
// API token the shell gave the backend. In a plain browser tab (dev) there
// is no shell and the dev backend requires no token.
let apiToken: string | null = null;
const tokenReady: Promise<string | null> = (async () => {
  if (!("__TAURI_INTERNALS__" in window)) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    apiToken = await invoke<string>("api_token");
  } catch {
    apiToken = null;
  }
  return apiToken;
})();

export async function authHeaders(): Promise<Record<string, string>> {
  const token = await tokenReady;
  return token ? { "X-IntelliFile-Token": token } : {};
}

/** A connection failure (engine not up yet, or gone) reads as a sentence, not "TypeError: Failed to fetch". */
export function describeError(e: unknown, path = ""): string {
  if (e instanceof DOMException && e.name === "AbortError") return "";
  if (e instanceof TypeError) return "The search engine isn't reachable yet — it starts with the app and takes about 30 seconds the first time.";
  const msg = e instanceof Error ? e.message : String(e);
  if (/ 401$/.test(msg)) return "The app and its engine don't share a session token — quit IntelliFile fully and open it again.";
  return path ? `${path}: ${msg}` : msg;
}

async function getJson<T>(path: string, params?: Record<string, string | number>, signal?: AbortSignal): Promise<T> {
  const url = new URL(`${BACKEND_URL}${path}`);
  if (params) for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  const res = await fetch(url.toString(), { headers: await authHeaders(), signal });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

export interface HealthResponse {
  status: string;
  app_data_dir: string;
}
export const checkBackendHealth = () => getJson<HealthResponse>("/health");

// ---- search ----
export type SearchMode = "auto" | "smart" | "exact" | "keyword";
// Phase 18: which retrieval route answered the query and what it cost.
export interface RouteStage { stage: string; ms?: number; skipped?: boolean; hits?: number; candidates?: number; relevant?: number; files?: number; to?: string; reason?: string }
export interface RouteReport {
  tier: string;
  requested_tier: string;
  escalated: boolean;
  complexity: number;
  reason: string;
  features: Record<string, unknown>;
  stages: RouteStage[];
  total_ms: number;
  corrected_query: string | null;
  suggest_ask?: boolean; // Phase 19: a question the retrieval tiers could not answer confidently
}
export type Confidence = "strong" | "weak";

export interface SearchResult {
  file_id: string;
  path: string | null;
  filename: string;
  // null when the file matched by name only (no content hit to score).
  score: number | null;
  keyword_score: number | null;
  semantic_score: number | null;
  reranker_score: number | null;
  page: number | null;
  matched_chunk: string;
  why: string[];
  confidence: Confidence;
  size: number;
  modified_time: number;
  // Phase 17: null when personalization is off or the profile is still cold.
  personal: { boost: number; signals: Record<string, number>; reasons: string[] } | null;
}
export const search = (q: string, mode: SearchMode = "auto", top_k = 20, signal?: AbortSignal) =>
  getJson<{ results: SearchResult[]; route: RouteReport | null }>("/search", { q, mode, top_k }, signal);

// What /search would silently correct the query to — null when it runs as typed.
export const suggest = (q: string) => getJson<{ suggestion: string | null }>("/suggest", { q });

export interface VisualSearchResult {
  file_id: string;
  path: string | null;
  filename: string;
  score: number;
  kind: string | null;
  captured_at: string | null;
  timestamp_offset_seconds: number | null;
  // Videos only: up to 3 distinct best-matching moments, best first — the filmstrip.
  moments: { t: number; score: number | null }[] | null;
  confidence: Confidence;
}
export type VisualKind = "all" | "photo" | "video";
export const searchVisual = (q: string, kind: VisualKind = "all", top_k = 24) =>
  getJson<{ results?: VisualSearchResult[]; error?: string; unrecognized?: string[]; corrected_query?: string | null }>(
    "/search-visual",
    kind === "all" ? { q, top_k } : { q, top_k, kind },
  );
export const suggestVisual = (q: string) => getJson<{ suggestion: string | null }>("/suggest-visual", { q });

// <img> tags cannot send headers, so the thumbnail URL carries the token as a query parameter.
export const thumbnailUrl = (path: string, size = 320, atSeconds: number | null = null) =>
  `${BACKEND_URL}/thumbnail?path=${encodeURIComponent(path)}&size=${size}${atSeconds != null ? `&t=${atSeconds}` : ""}${apiToken ? `&token=${apiToken}` : ""}`;

export function formatTimestamp(seconds: number): string {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

// ---- folders / indexing ----
export const indexFolder = (folder: string) => postJson<{ queued?: string; error?: string }>("/index-folder", { folder });
export const forgetFolder = (folder: string) => postJson<{ removed?: number; error?: string }>("/forget-folder", { folder });
export const reindexFolder = (folder: string) => postJson<{ queued?: string; error?: string }>("/reindex-folder", { folder });

export interface IndexJob {
  state: "idle" | "running" | "done" | "failed";
  folder: string | null;
  done: number;
  total: number;
  current_file: string | null;
  failed: number;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  queued: string[];
  paused_reason?: string | null; // Phase 10: set while the job is held for battery / low-power
  recent_failures?: { path: string; error: string; at: number }[]; // Phase 11
  recovered_at_start?: number; // Phase 11: files re-indexed after a crash mid-index
}
export type ResourceMode = "balanced" | "performance" | "battery_saver";
export interface PowerSnapshot {
  has_battery: boolean;
  on_battery: boolean;
  percent: number | null;
  low_power_mode: boolean;
  cpu_percent: number;
  app_cpu_percent: number;
  paused: boolean;
  paused_reason: string | null;
  mode: ResourceMode;
  pending_jobs: number;
}
export type AccessMode = "unset" | "all" | "limited" | "denied";
export interface AccessPolicy { mode: AccessMode; allows_indexing: boolean; whole_computer_roots: string[]; excluded_paths: string[] }
export const getAccess = () => getJson<AccessPolicy>("/access");
export const setAccess = (mode: Exclude<AccessMode, "unset">, remove_index = false) =>
  postJson<AccessPolicy & { previous: AccessMode; removed: number; error?: string }>("/access", { mode, remove_index });
export const indexFile = (path: string) => postJson<{ queued?: string; error?: string }>("/index-file", { folder: path });

export interface FolderStats {
  path: string;
  kind?: "folder" | "file";
  exists: boolean;
  indexing: boolean;
  queued: boolean;
  documents: number;
  photos: number;
  videos: number;
  audio: number;
}
export interface WorkingContext {
  session_id: string | null;
  started_at: number | null;
  events: number;
  queries: string[];
  files: string[];
  file_types: string[];
}
export interface StatusResponse {
  engine: string;
  job: IndexJob;
  folders: FolderStats[];
  totals: { documents: number; photos: number; videos: number; audio: number; files: number };
  index_size_bytes: number;
  // Phase 16: the activity memory behind personalization.
  activity: { enabled: boolean; events: number; session: WorkingContext };
  // Phase 10: battery / low-power / CPU and whether indexing is paused for it.
  power: PowerSnapshot;
  // Phase 12: the file-access policy chosen on first run.
  access: AccessPolicy;
  models: {
    text: { name: string; dimension: number; provider: string };
    photos: { name: string; precision: string; dimension: number } | null;
    speech: { name: string } | null;
    reranker: { name: string } | null;
  };
}
export const getStatus = () => getJson<StatusResponse>("/status");

// ---- activity memory (Phase 16) ----
export type EventKind = "result_clicked" | "file_opened" | "file_revealed" | "recommendation_clicked";
export interface UsageEvent {
  id: number;
  ts: number;
  kind: EventKind | "query";
  session_id: string;
  file_id: string | null;
  path: string | null;
  file_type: string | null;
  query: string | null;
  meta: Record<string, unknown> | null;
}
/** Fire-and-forget: what the user did with a result. Searches are recorded by the backend itself. */
export function recordEvent(kind: EventKind, fields: { file_id?: string | null; path?: string | null; query?: string | null }): void {
  postJson("/events", { kind, ...fields }).catch(() => { /* bookkeeping must never surface as an error */ });
}
export const listEvents = (limit = 50) => getJson<{ events: UsageEvent[]; total: number }>("/events", { limit });
export async function clearEvents(): Promise<{ cleared: number }> {
  const res = await fetch(`${BACKEND_URL}/events`, { method: "DELETE", headers: await authHeaders() });
  if (!res.ok) throw new Error(`/events failed: ${res.status}`);
  return res.json();
}
export interface AppSettings { remember_activity: boolean; personalize: boolean; pause_on_battery: boolean; pause_on_low_power: boolean; resource_mode: ResourceMode }

// ---- profile & recommendations (Phase 17) ----
export interface ProfileTopic { id: number; label: string; terms: string[]; files: { file_id: string; filename: string }[]; file_count: number; weight: number }
export interface ProfileResponse {
  built_at: number;
  events: number;
  sessions: number;
  cold_start: boolean;
  cold_start_threshold: number;
  top_files: { file_id: string; path: string; filename: string; score: number; last_touched: number | null; usual_slot: string | null }[];
  type_shares: Record<string, number>;
  topics: ProfileTopic[];
  heatmap: number[][]; // [weekday][slot]
  slot_hours: number;
  now_slot: { weekday: number; slot: number; label: string };
}
export const getProfile = () => getJson<ProfileResponse>("/profile");
export interface Recommendation { file_id: string; path: string; filename: string; reason: string }
export interface RecommendationsResponse {
  enabled: boolean;
  cold_start: boolean | null;
  now: string | null;
  likely_next: Recommendation[];
  usual_now: Recommendation[];
  recent: Recommendation[];
}
export const getRecommendations = () => getJson<RecommendationsResponse>("/recommendations");
// Phase 20: live router statistics from the remembered queries.
export interface RouterStats { total: number; escalated: number; routes: { route: string; queries: number; share: number; mean_ms: number; with_results: number }[] }
export const getRouterStats = () => getJson<RouterStats>("/router-stats");
export interface RouterModel { available: boolean; active: boolean; trained_on: number; trained_at: number | null; accuracy: { holdout: number; rules: number; learned: number } | null; confidence: number; queries_available: number; needed: number }
export const getRouterModel = () => getJson<RouterModel>("/router/model");
export const getSettings = () => getJson<AppSettings>("/settings");
export const updateSettings = (changes: Partial<AppSettings>) => postJson<AppSettings>("/settings", changes);

// ---- ask mode (Phase 19: the local LLM agent) ----
export interface AskSource { number: number; file_id: string; path: string; filename: string; page: number | null; snippet: string; confidence: string; why: string[] }
export type AskEvent =
  | { type: "context"; context: { session: WorkingContext | null; topics: string[]; top_files: string[] } }
  | { type: "thought"; text: string; system?: boolean; ms?: number }
  | { type: "tool_call"; tool: string; args: Record<string, unknown>; call: number }
  | { type: "tool_result"; tool: string; sources: AskSource[]; route?: RouteReport; call: number }
  | { type: "answer_start" }
  | { type: "token"; text: string }
  | { type: "answer"; text: string; citations: AskSource[]; grounded: boolean; citations_inferred?: boolean; warnings?: string[] }
  | { type: "done"; seconds: number; tool_calls: number; strategies: { query: string; mode: string; filters: string; route: string; results: number }[]; sources: AskSource[] }
  | { type: "error"; message: string };

/** Streams the agent's trace for a question; resolves when the stream ends. */
export async function askStream(q: string, onEvent: (e: AskEvent) => void, signal?: AbortSignal): Promise<void> {
  const url = new URL(`${BACKEND_URL}/ask`);
  url.searchParams.set("q", q);
  const res = await fetch(url.toString(), { signal, headers: await authHeaders() });
  if (!res.ok || !res.body) throw new Error(`/ask failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as AskEvent);
    }
  }
}
export const askStatus = () => getJson<{ available: boolean; model: string | null; loaded: boolean; tokens_per_second: number | null }>("/ask/status");

// ---- voice ----
// `heard` is set only when the backend snapped a misheard file name (2026-09-20).
// `suggestion` is a sound-alike file name offered when the raw words already find something.
export async function transcribeAudio(wav: Blob): Promise<{ text?: string; heard?: string | null; suggestion?: string | null; error?: string }> {
  const form = new FormData();
  form.append("audio", wav, "recording.wav");
  const res = await fetch(`${BACKEND_URL}/transcribe`, { method: "POST", body: form, headers: await authHeaders() });
  if (!res.ok) throw new Error(`Transcription request failed: ${res.status}`);
  return res.json();
}

// ---- formatting helpers shared by pages ----
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatAgo(epochSeconds: number): string {
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export type FileGroup = "text" | "doc" | "audio" | "video" | "image" | "sheet" | "slides" | "code";

// Mirrors backend/app/files/discovery.py's extension groups (2026-09-20).
const CODE_EXTS = ["PY", "JS", "TS", "TSX", "JSX", "JAVA", "C", "CPP", "H", "HPP", "CS", "GO", "RS", "RB", "PHP", "SWIFT", "KT",
  "CSS", "JSON", "XML", "YAML", "YML", "TOML", "INI", "CFG", "SH", "BAT", "PS1", "SQL", "HTML", "HTM"];

export function fileKind(filename: string): { badge: string; group: FileGroup } {
  const ext = (filename.split(".").pop() || "").toUpperCase();
  if (["M4A", "MP3", "WAV", "FLAC", "OGG", "AIFF", "AIF"].includes(ext)) return { badge: ext, group: "audio" };
  if (["JPG", "JPEG", "PNG", "GIF", "BMP", "WEBP", "AVIF"].includes(ext)) return { badge: ext, group: "image" };
  if (["MP4", "MOV", "M4V", "MKV", "WEBM", "AVI"].includes(ext)) return { badge: ext, group: "video" };
  if (["PDF", "DOCX", "DOC", "RTF"].includes(ext)) return { badge: ext, group: "doc" };
  if (["CSV", "TSV", "XLSX", "XLSM"].includes(ext)) return { badge: ext, group: "sheet" };
  if (ext === "PPTX") return { badge: ext, group: "slides" };
  if (CODE_EXTS.includes(ext)) return { badge: ext, group: "code" };
  return { badge: ext || "FILE", group: "text" };
}

export function shortenPath(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, "~").replace(/^C:\\Users\\[^\\]+/, "~");
}

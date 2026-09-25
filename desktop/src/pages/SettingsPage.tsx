import { useEffect, useState } from "react";
import pkg from "../../package.json";
import { ask } from "@tauri-apps/plugin-dialog";
import { AccessMode, AppSettings, clearEvents, formatAgo, formatBytes, getSettings, listEvents, ResourceMode, setAccess, shortenPath, StatusResponse, updateSettings, UsageEvent } from "../backend";
import { FILE_MANAGER, MOD_KEY } from "../platform";

interface Props {
  status: StatusResponse | null;
  appDataDir: string | null;
  onError: (m: string | null) => void;
  refresh: () => Promise<void>;
}

function Toggle({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent/70" : "bg-white/15"} ${disabled ? "opacity-50" : ""}`}
    >
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

const EVENT_LABEL: Record<string, string> = {
  query: "Searched",
  result_clicked: "Selected",
  file_opened: "Opened",
  file_revealed: `Revealed in ${FILE_MANAGER}`,
  recommendation_clicked: "Picked a recommendation",
};

const MODES: { id: ResourceMode; label: string; note: string }[] = [
  { id: "balanced", label: "Balanced", note: "Index promptly; pause on battery if the switches say so" },
  { id: "performance", label: "Performance", note: "Never pause for battery or low-power mode" },
  { id: "battery_saver", label: "Battery Saver", note: "Pause between files and hold live re-indexing while on battery" },
];

const ACCESS_LABEL: Record<AccessMode, string> = { unset: "not chosen yet", all: "Allow all — whole computer", limited: "Allow limited — chosen folders and files", denied: "Denied — nothing is indexed" };

/** Phase 12 — the file-access policy, changeable after first run. Downgrading asks whether to drop the index. */
function AccessPanel({ status, onError, refresh }: { status: StatusResponse | null; onError: (m: string | null) => void; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const mode = status?.access?.mode ?? "unset";
  async function change(next: Exclude<AccessMode, "unset">) {
    if (next === mode) return;
    let remove = false;
    if (mode === "all" && next !== "all") {
      let sure = false;
      try { sure = await ask("Also remove everything already indexed? Keep = files stay searchable but nothing new is scanned outside what you allow.", { title: "Remove the existing index?", kind: "warning", okLabel: "Remove index", cancelLabel: "Keep" }); }
      catch { sure = window.confirm("Also remove everything already indexed?"); }
      remove = sure;
    }
    setBusy(true);
    try { const r = await setAccess(next, remove); if (r.error) onError(r.error); await refresh(); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return (
    <div className="panel p-5">
      <div className="text-[15px] font-semibold">File access</div>
      <p className="mt-1 text-[13.5px] text-white/60">What IntelliFile is allowed to read. Currently: <b className="text-white/85">{ACCESS_LABEL[mode]}</b>.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {(["all", "limited", "denied"] as const).map((m) => (
          <button key={m} className={`rounded-lg px-3 py-2 text-[13px] transition-colors ${mode === m ? "bg-accent/15 text-accent" : "bg-white/[0.04] text-white/70 hover:text-white"}`} onClick={() => change(m)} disabled={busy}>
            {ACCESS_LABEL[m].split(" — ")[0]}
          </button>
        ))}
      </div>
      {mode === "all" && status?.access && <div className="mono mt-2 text-[11px] text-white/40">roots: {status.access.whole_computer_roots.map(shortenPath).join(", ")} · skipped: {status.access.excluded_paths.map(shortenPath).join(", ")}</div>}
    </div>
  );
}

/** Phase 10 — power-aware indexing: the pause switches and the resource mode, applied by the backend at once. */
function PowerPanel({ status, onError }: { status: StatusResponse | null; onError: (m: string | null) => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { getSettings().then(setSettings).catch((e) => onError(e instanceof Error ? e.message : String(e))); }, [onError]);

  async function change(patch: Partial<AppSettings>) {
    setBusy(true);
    try { setSettings(await updateSettings(patch)); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const power = status?.power;
  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[15px] font-semibold">Indexing & power</div>
          <p className="mt-1 text-[13.5px] text-white/60">Indexing pauses between files and resumes from the same file when power returns — nothing is rescanned.</p>
        </div>
        {power && (
          <span className={`chip ${power.paused ? "chip-personal" : ""}`}>
            {power.paused ? `paused — ${power.paused_reason}` : power.has_battery ? (power.on_battery ? `on battery · ${Math.round(power.percent ?? 0)}%` : "plugged in") : "no battery"}
          </span>
        )}
      </div>
      <div className="mt-3 space-y-2">
        <div className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div><div className="text-[13.5px] text-white/85">Pause indexing on battery</div><div className="mt-0.5 text-[12px] text-white/50">Search keeps working; new files wait until you plug in.</div></div>
          <Toggle on={settings?.pause_on_battery ?? true} onChange={(v) => change({ pause_on_battery: v })} disabled={busy || !settings} label="Pause indexing on battery" />
        </div>
        <div className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div><div className="text-[13.5px] text-white/85">Pause when the system is in Low Power / Battery Saver mode</div><div className="mt-0.5 text-[12px] text-white/50">Follows the OS setting, plugged in or not.</div></div>
          <Toggle on={settings?.pause_on_low_power ?? true} onChange={(v) => change({ pause_on_low_power: v })} disabled={busy || !settings} label="Pause in low power mode" />
        </div>
        <div className="rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div className="text-[13.5px] text-white/85">Resource mode</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {MODES.map((m) => (
              <button
                key={m.id}
                className={`rounded-lg px-3 py-2 text-left transition-colors ${settings?.resource_mode === m.id ? "bg-accent/15 text-accent" : "bg-white/[0.04] text-white/70 hover:text-white"}`}
                onClick={() => change({ resource_mode: m.id })}
                disabled={busy || !settings}
                title={m.note}
              >
                <div className="text-[13px] font-medium">{m.label}</div>
                <div className="mt-0.5 max-w-[16rem] text-[11px] text-white/45">{m.note}</div>
              </button>
            ))}
          </div>
        </div>
      </div>
      {power && (
        <div className="mono mt-3 text-[11px] text-white/40">
          CPU: machine {Math.round(power.cpu_percent)}% · IntelliFile {power.app_cpu_percent.toFixed(1)}%{power.low_power_mode ? " · OS low power mode on" : ""}{power.pending_jobs ? ` · ${power.pending_jobs} file(s) waiting` : ""}
        </div>
      )}
    </div>
  );
}

/** Phase 16: the activity memory behind personalization — switch, live session, recent events, clear. */
function ActivityPanel({ status, onError }: { status: StatusResponse | null; onError: (m: string | null) => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [personalize, setPersonalize] = useState<boolean | null>(null);
  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const [settings, recent] = await Promise.all([getSettings(), listEvents(8)]);
      setEnabled(settings.remember_activity);
      setPersonalize(settings.personalize);
      setEvents(recent.events);
      setTotal(recent.total);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => { load(); }, [status?.activity?.events]); // refresh when the count moves

  async function toggle(v: boolean) {
    setBusy(true);
    try { setEnabled((await updateSettings({ remember_activity: v })).remember_activity); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function togglePersonalize(v: boolean) {
    setBusy(true);
    try { setPersonalize((await updateSettings({ personalize: v })).personalize); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function clear() {
    setBusy(true);
    try { await clearEvents(); await load(); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const session = status?.activity.session;
  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[15px] font-semibold">Activity memory</div>
          <p className="mt-1 text-[13.5px] text-white/60">What you search for and open, kept on this computer, so results and recommendations can learn what matters to you. Nothing is sent anywhere.</p>
        </div>
        <Toggle on={enabled ?? true} onChange={toggle} disabled={busy || enabled === null} label="Remember my activity" />
      </div>
      <div className="mt-3 flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2.5">
        <div>
          <div className="text-[13.5px] text-white/85">Personalize results and recommendations</div>
          <div className="mt-0.5 text-[12px] text-white/50">Files you use often, at this time of day, of your usual types and topics rank a little higher — each says why. Off = pure retrieval order. See Insights for what has been learned.</div>
        </div>
        <Toggle on={personalize ?? true} onChange={togglePersonalize} disabled={busy || personalize === null} label="Personalize results and recommendations" />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <div className="min-w-0 rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div className="mono text-[11px] uppercase tracking-wider text-white/45">Remembered</div>
          <div className="mt-1 text-[20px] font-semibold">{total.toLocaleString()} <span className="text-[12px] font-normal text-white/50">events</span></div>
        </div>
        <div className="min-w-0 rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div className="mono text-[11px] uppercase tracking-wider text-white/45">This session</div>
          <div className="mt-1 text-[20px] font-semibold">{session?.events ?? 0} <span className="text-[12px] font-normal text-white/50">{session?.session_id ? `since ${formatAgo(session.started_at ?? 0)}` : "idle"}</span></div>
        </div>
        <div className="min-w-0 rounded-lg bg-white/[0.03] px-3 py-2.5">
          <div className="mono text-[11px] uppercase tracking-wider text-white/45">Working on</div>
          <div className="mt-1 truncate text-[13px] text-white/85">{session?.files.length ? session.files.map((f) => f.split(/[\\/]/).pop()).join(", ") : session?.queries.length ? `“${session.queries[session.queries.length - 1]}”` : "—"}</div>
        </div>
      </div>
      {events.length > 0 && (
        <div className="mt-3 space-y-1">
          {events.map((e) => (
            <div key={e.id} className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-1.5 text-[12.5px]">
              <span className="chip">{EVENT_LABEL[e.kind] ?? e.kind}</span>
              <span className="truncate text-white/80">{e.kind === "query" ? `“${e.query}”` : e.path ? shortenPath(e.path) : e.file_id}</span>
              <span className="mono ml-auto shrink-0 text-[11px] text-white/40">{formatAgo(e.ts)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 flex items-center justify-between">
        <span className="mono text-[11px] text-white/40">{enabled === false ? "Paused — nothing new is recorded" : "Sessions split after 30 minutes of inactivity"}</span>
        <button className="btn-ghost px-3 py-1.5 text-[13px] hover:text-error" onClick={clear} disabled={busy || total === 0}>
          <span className="material-symbols-outlined mr-1 align-middle icon-sm">delete_sweep</span>Clear activity
        </button>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2.5">
      <span className="text-[13.5px] text-white/60">{label}</span>
      <span className="mono text-[13px] text-white/85">{value}</span>
    </div>
  );
}

const SHORTCUTS: [string, string][] = [
  ["Ctrl+Space", "Open quick search from anywhere"],
  ["↑ / ↓", "Move between results"],
  ["Enter", "Open the selected result"],
  [`${MOD_KEY}+Enter`, `Reveal the selected result in ${FILE_MANAGER}`],
  ["Escape", "Clear the search box, then close"],
];

/**
 * Mostly informational: the Activity memory switch (Phase 16) is the first
 * real control; resource-mode (Phase 10), personalization (Phase 17) and
 * reranking (Phase 18) toggles join it as those backend features land.
 */
export function SettingsPage({ status, appDataDir, onError, refresh }: Props) {
  const models = status?.models;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
      <div>
        <h2 className="text-[26px] font-semibold tracking-tight">Settings</h2>
        <p className="mt-1 text-[14px] text-white/55">Everything here runs on this computer. There is nothing to sign in to and nothing to configure remotely.</p>
      </div>

      <AccessPanel status={status} onError={onError} refresh={refresh} />
      <PowerPanel status={status} onError={onError} />
      <ActivityPanel status={status} onError={onError} />

      <div className="panel p-5">
        <div className="text-[15px] font-semibold">About</div>
        <div className="mt-3 space-y-1.5">
          <Row label="Version" value={pkg.version} />
          <Row label="Index location" value={appDataDir ?? "—"} />
          <Row label="Index size on disk" value={status ? formatBytes(status.index_size_bytes) : "—"} />
          <Row label="Indexed files" value={status ? status.totals.files.toLocaleString() : "—"} />
        </div>
      </div>

      <div className="panel p-5">
        <div className="text-[15px] font-semibold">Active models</div>
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/[0.04] text-accent"><span className="material-symbols-outlined icon-sm">notes</span></span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-medium">{models?.text.name ?? "—"}</div>
              <div className="mono mt-0.5 text-[11px] text-white/45">Text semantic search · {models?.text.dimension ?? "—"}-dim · {models?.text.provider ?? "—"}</div>
            </div>
          </div>
          <div className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/[0.04] text-accent"><span className="material-symbols-outlined icon-sm">image</span></span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-medium">{models?.photos?.name ?? "Not installed"}</div>
              <div className="mono mt-0.5 text-[11px] text-white/45">
                {models?.photos ? `Photo search · ${models.photos.dimension}-dim · ${models.photos.precision}` : "Photo search unavailable without this model"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/[0.04] text-accent"><span className="material-symbols-outlined icon-sm">graphic_eq</span></span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-medium">{models?.speech?.name ?? "Not installed"}</div>
              <div className="mono mt-0.5 text-[11px] text-white/45">
                {models?.speech ? "Voice search + spoken audio transcripts" : "Voice search unavailable without this model"}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="panel p-5">
        <div className="text-[15px] font-semibold">Keyboard shortcuts</div>
        <div className="mt-3 space-y-1.5">
          {SHORTCUTS.map(([key, desc]) => (
            <div key={key} className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2.5">
              <span className="text-[13.5px] text-white/60">{desc}</span>
              <span className="kbd">{key}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel flex items-start gap-4 p-5">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white/[0.04]"><span className="material-symbols-outlined text-accent">lock</span></div>
        <div>
          <div className="flex items-center gap-2 text-[15px] font-semibold">100% on-device privacy<span className="chip chip-accent">local only</span></div>
          <p className="mt-1 text-[13.5px] text-white/60">No telemetry, no cloud calls, no account. Your activity memory above stays in the index folder on this computer and can be cleared at any time.</p>
        </div>
      </div>
    </div>
  );
}

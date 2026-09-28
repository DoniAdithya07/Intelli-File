import { ReactNode, useEffect, useState } from "react";
import pkg from "../../package.json";
import { ask } from "@tauri-apps/plugin-dialog";
import { AccessMode, AppSettings, askStatus, clearEvents, formatBytes, getSettings, getWindowsRecent, ResourceMode, setAccess, shortenPath, StatusResponse, updateSettings, WindowsRecentStatus } from "../backend";
import { LegalDoc, PRIVACY, TERMS } from "../legal";
import { ThemeChoice, useTheme } from "../theme";
import { PageHeader, Section, Toggle } from "../ui/kit";

interface Props {
  status: StatusResponse | null;
  appDataDir: string | null;
  onError: (m: string | null) => void;
  refresh: () => Promise<void>;
  onOpen: (page: "index" | "activity") => void;
}

/** One setting: its name and what it does on the left, the control on the right. */
function Row({ title, note, children }: { title: string; note?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-rule py-2.5 first:pt-0 last:border-b-0 last:pb-0">
      <div className="min-w-0">
        <div className="text-[13.5px]">{title}</div>
        {note && <div className="mt-0.5 text-[12px] text-ink/60">{note}</div>}
      </div>
      {children && <div className="shrink-0">{children}</div>}
    </div>
  );
}

/** Radio choices drawn as a joined row of rectangular buttons. */
function Choice<T extends string>({ label, options, value, onChange, disabled }: { label: string; options: { id: T; label: string; hint?: string }[]; value: T | null; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex overflow-hidden rounded-md border border-rule-strong">
      {options.map((o, i) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={value === o.id}
          title={o.hint}
          disabled={disabled}
          onClick={() => onChange(o.id)}
          className={`px-3 py-1.5 text-[13px] ${i > 0 ? "border-l border-rule-strong" : ""} ${value === o.id ? "bg-accent text-on-accent" : "text-ink/80 hover:bg-ink/[0.05]"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: "system", label: "Same as Windows" },
  { id: "day", label: "Day" },
  { id: "night", label: "Night" },
];
const ACCESS: { id: Exclude<AccessMode, "unset">; label: string; hint: string }[] = [
  { id: "all", label: "Whole computer", hint: "Your folders and drives; system folders skipped" },
  { id: "limited", label: "Only folders I choose", hint: "Only the folders and files added in Index" },
  { id: "denied", label: "Nothing", hint: "Nothing is indexed and search is off" },
];
const MODES: { id: ResourceMode; label: string; hint: string }[] = [
  { id: "balanced", label: "Balanced", hint: "Index promptly; pause on battery if the switches say so" },
  { id: "performance", label: "Performance", hint: "Never pause for battery or power saving" },
  { id: "battery_saver", label: "Battery saver", hint: "Pause between files and hold live re-indexing on battery" },
];

export function SettingsPage({ status, appDataDir, onError, refresh, onOpen }: Props) {
  const [theme, setTheme] = useTheme();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [recent, setRecent] = useState<WindowsRecentStatus | null>(null);
  const [askModel, setAskModel] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [legal, setLegal] = useState<LegalDoc | null>(null);

  useEffect(() => {
    getSettings().then(setSettings).catch((e) => onError(e instanceof Error ? e.message : String(e)));
    getWindowsRecent().then(setRecent).catch(() => setRecent(null));
    askStatus().then((s) => setAskModel(s.available ? s.model : null)).catch(() => setAskModel(null));
  }, [onError]);

  // A setting that fails to save keeps its old value and says so (section 23).
  async function change(patch: Partial<AppSettings>) {
    setBusy(true);
    try {
      setSettings(await updateSettings(patch));
      if ("import_windows_recent" in patch) window.setTimeout(() => getWindowsRecent().then(setRecent).catch(() => undefined), 1500);
    } catch (e) {
      onError(`The setting could not be saved, so it was left as it was. ${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  // Clear activity (UI_DESIGN section 12): asks first; nothing is cleared without the desktop dialog.
  async function clearActivity() {
    let sure = false;
    try { sure = await ask("Remove every remembered search and opened file? For You starts learning again from nothing.", { title: "Clear activity", kind: "warning", okLabel: "Clear", cancelLabel: "Keep" }); }
    catch { sure = false; }
    if (!sure) return;
    setBusy(true);
    try { await clearEvents(); }
    catch (e) { onError(`Activity could not be cleared. ${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  const mode = status?.access?.mode ?? null;
  async function changeAccess(next: Exclude<AccessMode, "unset">) {
    if (next === mode) return;
    let remove = false;
    if (mode === "all") {
      try { remove = await ask("Also remove everything already indexed? Keep leaves those files searchable; nothing new is read outside what you allow.", { title: "Remove the existing index?", kind: "warning", okLabel: "Remove index", cancelLabel: "Keep" }); }
      catch { remove = false; }
    }
    setBusy(true);
    try { const r = await setAccess(next, remove); if (r.error) onError(r.error); await refresh(); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const power = status?.power;
  const models = status?.models;
  const modelRows: [string, string | null | undefined][] = [
    ["Text search", models?.text?.name],
    ["Photos", models?.photos?.name ?? null],
    ["Speech", models?.speech?.name ?? null],
    ["Reranker", models?.reranker?.name ?? null],
    ["Text in images", models?.ocr ? `${models.ocr.name} (${models.ocr.language})` : null],
    ["Ask", askModel],
  ];

  return (
    <div className="h-full overflow-y-auto pr-1">
      <PageHeader title="Settings" subtitle="Everything here is saved on this computer. There is nothing to sign in to." />
      <div className="mt-4 columns-2 gap-4 [&>*]:mb-4 [&>*]:break-inside-avoid">
        <Section title="Appearance">
          <Choice label="Theme" options={THEMES} value={theme} onChange={setTheme} />
        </Section>

        <Section title="Search">
          <Row title="Personalize results" note="Among results that are nearly tied, files you use more rank higher, and each says why.">
            <Toggle on={settings?.personalize ?? true} onChange={(v) => change({ personalize: v })} disabled={busy || !settings} label="Personalize results" />
          </Row>
        </Section>

        <Section title="File access" note={mode === "all" && status ? <>Reads <span className="mono">{status.access.whole_computer_roots.map(shortenPath).join(", ")}</span>; system folders are skipped.</> : "What IntelliFile may read."}>
          <Choice label="File access" options={ACCESS} value={mode === "unset" ? null : mode} onChange={changeAccess} disabled={busy || !mode} />
          <button className="mt-3 block text-[13px] text-ink/70 hover:text-ink hover:underline" onClick={() => onOpen("index")}>Manage indexed folders</button>
        </Section>

        <Section title="Privacy">
          <Row title="Remember my activity" note="Searches and opened files, kept on this computer for For You and ranking.">
            <Toggle on={settings?.remember_activity ?? true} onChange={(v) => change({ remember_activity: v })} disabled={busy || !settings} label="Remember my activity" />
          </Row>
          {recent?.available && (
            <Row
              title="Learn from files you opened in Windows"
              note={recent.enabled && recent.last ? `${recent.last.indexed_files} recently opened files are indexed here; ${recent.last.events_added} opens added.` : "Reads Windows' Recent items list so For You can start today. Off by default."}
            >
              <Toggle on={recent.enabled} onChange={(v) => change({ import_windows_recent: v })} disabled={busy || !settings?.remember_activity} label="Learn from files you opened in Windows" />
            </Row>
          )}
          <Row title="Activity history" note="See or clear everything that was remembered.">
            <div className="flex gap-2">
              <button className="btn-secondary px-3 py-1 text-[13px]" onClick={() => onOpen("activity")}>Open Activity</button>
              <button className="btn-secondary px-3 py-1 text-[13px]" onClick={clearActivity} disabled={busy}>Clear activity</button>
            </div>
          </Row>
        </Section>

        <Section title="Indexing" note={power ? (power.paused ? `Paused: ${power.paused_reason}` : power.has_battery ? (power.on_battery ? `On battery, ${Math.round(power.percent ?? 0)}%` : "Plugged in") : "No battery") : undefined}>
          <Row title="Pause on battery" note="Search keeps working; new files wait until you plug in.">
            <Toggle on={settings?.pause_on_battery ?? true} onChange={(v) => change({ pause_on_battery: v })} disabled={busy || !settings} label="Pause on battery" />
          </Row>
          <Row title="Pause in power-saving mode" note="Follows Windows' battery saver, plugged in or not.">
            <Toggle on={settings?.pause_on_low_power ?? true} onChange={(v) => change({ pause_on_low_power: v })} disabled={busy || !settings} label="Pause in power-saving mode" />
          </Row>
          <div className="pt-2.5">
            <div className="mb-1.5 text-[13.5px]">Resource mode</div>
            <Choice label="Resource mode" options={MODES} value={settings?.resource_mode ?? null} onChange={(m) => change({ resource_mode: m })} disabled={busy || !settings} />
          </div>
        </Section>

        <Section title="Local models" note="They run on this computer's processor.">
          <ul className="space-y-1.5 text-[13px]">
            {modelRows.map(([label, name]) => (
              <li key={label} className="flex items-center gap-3">
                <span className="w-28 shrink-0">{label}</span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink/60">{name ?? ""}</span>
                <span className={`shrink-0 text-[12px] ${name ? "text-ink/75" : name === null ? "text-error" : "text-ink/50"}`}>{name ? "Installed" : name === null ? "Not installed" : "Checking"}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Network">
          <p className="text-[13px] text-ink/80">IntelliFile never connects to the internet. There is no account, no update check and no usage reporting; every model runs on this computer.</p>
        </Section>

        <Section title="About">
          <dl className="space-y-1.5 text-[13px]">
            <div className="flex justify-between gap-3"><dt className="text-ink/65">Version</dt><dd className="mono">{pkg.version}</dd></div>
            <div className="flex justify-between gap-3"><dt className="shrink-0 text-ink/65">Index location</dt><dd className="mono truncate" title={appDataDir ?? undefined}>{appDataDir ? shortenPath(appDataDir) : "Loading"}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-ink/65">Index size</dt><dd className="mono">{status ? formatBytes(status.index_size_bytes) : "Loading"}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-ink/65">Licence</dt><dd>MIT, open source</dd></div>
          </dl>
          <div className="mt-3 flex gap-2">
            <button className="btn-secondary px-3 py-1 text-[13px]" onClick={() => setLegal(PRIVACY)}>Privacy policy</button>
            <button className="btn-secondary px-3 py-1 text-[13px]" onClick={() => setLegal(TERMS)}>Terms of use</button>
          </div>
        </Section>
      </div>

      {legal && <LegalDialog doc={legal} onClose={() => setLegal(null)} />}
    </div>
  );
}

/** Privacy policy or terms of use, readable in place (text from ../legal.ts). */
function LegalDialog({ doc, onClose }: { doc: LegalDoc; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={doc.title} className="anim-dialog panel flex max-h-full w-full max-w-[680px] flex-col shadow-palette" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-baseline justify-between border-b border-rule px-6 py-4">
          <div>
            <h3 className="text-[18px] font-semibold">{doc.title}</h3>
            <div className="text-[12px] text-ink/60">Last updated {doc.updated}</div>
          </div>
          <button className="btn-ghost px-2 py-1 text-[13px]" onClick={onClose} autoFocus>Close</button>
        </div>
        <div className="overflow-y-auto px-6 py-4">
          {doc.sections.map((sec) => (
            <section key={sec.heading} className="mb-4 max-w-[68ch]">
              <h4 className="text-[14px] font-semibold">{sec.heading}</h4>
              {sec.body.map((para, i) => <p key={i} className="mt-1.5 text-[14px] leading-relaxed text-ink/80 select-text">{para}</p>)}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

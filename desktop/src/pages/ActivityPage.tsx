import { useCallback, useEffect, useState } from "react";
import { ask } from "@tauri-apps/plugin-dialog";
import { clearEvents, getSettings, listEvents, updateSettings, UsageEvent } from "../backend";
import { FILE_MANAGER } from "../platform";
import { FileIcon, PageHeader, Toggle } from "../ui/kit";

// All five kinds the backend records (docs/UI_DESIGN.md section 9).
const KINDS: { id: string; label: string; action: string }[] = [
  { id: "query", label: "Searches", action: "Search" },
  { id: "file_opened", label: "Opened", action: "Opened" },
  { id: "file_revealed", label: `Shown in ${FILE_MANAGER}`, action: `Shown in ${FILE_MANAGER}` },
  { id: "result_clicked", label: "Selected results", action: "Selected" },
  { id: "recommendation_clicked", label: "Opened from recommendations", action: "From recommendations" },
];

function dayLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

export function ActivityPage({ onError }: { onError: (m: string | null) => void }) {
  const [events, setEvents] = useState<UsageEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [kind, setKind] = useState("all");
  const [remember, setRemember] = useState<boolean | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ev, settings] = await Promise.all([listEvents(300), getSettings()]);
      setEvents(ev.events); setTotal(ev.total); setRemember(settings.remember_activity); setFailed(false);
    } catch { setFailed(true); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function toggle(v: boolean) {
    setBusy(true);
    try { setRemember((await updateSettings({ remember_activity: v })).remember_activity); }
    catch (e) { onError(`The setting could not be saved: ${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }
  async function clear() {
    let sure = false;
    try { sure = await ask("Remove every remembered search and opened file? Personalization starts learning again from nothing.", { title: "Clear activity", kind: "warning", okLabel: "Clear", cancelLabel: "Keep" }); }
    catch { sure = false; } // outside the desktop shell there is no dialog, so nothing is cleared
    if (!sure) return;
    setBusy(true);
    try { await clearEvents(); await load(); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const shown = (events ?? []).filter((e) => kind === "all" || e.kind === kind);
  const groups: [string, UsageEvent[]][] = [];
  for (const e of shown) {
    const label = dayLabel(e.ts);
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(e); else groups.push([label, [e]]);
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <PageHeader title="Activity" subtitle="Your recent searches and the files you opened, kept only on this computer.">
        <label className="sr-only" htmlFor="activity-kind">Show</label>
        <select id="activity-kind" className="rounded-md border border-rule-strong bg-content px-2 py-1.5 text-[13px]" value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
          <option value="all">All activity</option>
          {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
        </select>
        <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={clear} disabled={busy || total === 0}>Clear activity</button>
      </PageHeader>

      <div className="flex items-center justify-between rounded-lg border border-rule bg-content px-4 py-3">
        <div>
          <div className="text-[14px] font-medium">Remember my activity</div>
          <div className="text-[12px] text-ink/65">{remember === false ? "Off: nothing new is recorded, and For You stops learning." : `${total.toLocaleString()} actions remembered. Used for ranking near-ties, recommendations and Ask's context.`}</div>
        </div>
        <Toggle on={remember ?? true} onChange={toggle} disabled={busy || remember === null} label="Remember my activity" />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {failed && (
          <div role="alert" className="text-[13px] text-error">Activity could not be loaded. <button className="btn-secondary ml-2 px-2 py-0.5 text-[12px]" onClick={load}>Retry</button></div>
        )}
        {!events && !failed && <div className="space-y-2">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-9" />)}</div>}
        {events && shown.length === 0 && (
          <p className="py-10 text-center text-[14px] text-ink/65">{remember === false ? "Remember my activity is off." : "No activity yet."}</p>
        )}
        {groups.map(([label, list]) => (
          <section key={label} className="mb-4">
            <h2 className="mb-1.5 text-[13px] font-semibold">{label}</h2>
            <ul className="overflow-hidden rounded-lg border border-rule bg-content">
              {list.map((e) => {
                const k = KINDS.find((x) => x.id === e.kind);
                const name = e.path ? e.path.split(/[\\/]/).pop()! : null;
                return (
                  <li key={e.id} className="flex items-center gap-4 border-b border-rule px-4 py-2 text-[13px] last:border-b-0">
                    <span className="mono w-[4.5rem] shrink-0 whitespace-nowrap text-[12px] text-ink/65">{new Date(e.ts * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                    <span className="w-40 shrink-0 text-ink/70">{k?.action ?? e.kind}</span>
                    {e.kind === "query" ? (
                      <span className="min-w-0 truncate">"{e.query}"</span>
                    ) : (
                      <span className="flex min-w-0 items-center gap-2">
                        {name && <FileIcon filename={name} size={22} />}
                        <span className="truncate" title={e.path ?? undefined}>{name ?? e.file_id}</span>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

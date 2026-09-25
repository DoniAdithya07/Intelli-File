import { useState } from "react";
import { ask, open } from "@tauri-apps/plugin-dialog";
import { AccessMode, forgetFolder, formatBytes, indexFile, indexFolder, reindexFolder, setAccess, shortenPath, StatusResponse } from "../backend";

interface Props {
  status: StatusResponse | null;
  onError: (m: string | null) => void;
  refresh: () => Promise<void>;
}

function Stat({ label, value, unit, icon, bar }: { label: string; value: string; unit?: string; icon: string; bar: number }) {
  return (
    <div className="panel p-4">
      <div className="mono flex items-center justify-between text-[11px] uppercase tracking-wider text-white/45">
        {label}<span className="material-symbols-outlined icon-sm text-accent">{icon}</span>
      </div>
      <div className="mt-2 text-[26px] font-semibold leading-none">
        {value}{unit && <span className="mono ml-1 text-[12px] text-accent">{unit}</span>}
      </div>
      <div className="progress mt-3"><i style={{ width: `${Math.max(4, Math.min(100, bar))}%`, animation: "none" }} /></div>
    </div>
  );
}

const ACCESS_CHOICES: { id: Exclude<AccessMode, "unset">; title: string; body: string; icon: string }[] = [
  { id: "all", title: "Allow all", body: "Search every file on this computer: your home folder and any attached drives. System folders, app data and caches are skipped.", icon: "computer" },
  { id: "limited", title: "Allow limited", body: "You choose which folders and which single files IntelliFile may read. Nothing else is touched.", icon: "folder_special" },
  { id: "denied", title: "Deny", body: "Index nothing. Search stays off until you change this in Settings → File access.", icon: "block" },
];

/** Phase 12: the first-run question. Nothing is scanned until it is answered. */
export function AccessScreen({ onChoose, busy }: { onChoose: (m: Exclude<AccessMode, "unset">) => void; busy: boolean }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="panel max-w-2xl p-10">
        <div className="mx-auto mb-5 grid h-16 w-16 place-items-center rounded-2xl btn-gradient">
          <span className="material-symbols-outlined text-accent" style={{ fontSize: 32 }}>shield</span>
        </div>
        <h2 className="text-center text-[22px] font-semibold tracking-tight">IntelliFile needs access to your files to search them</h2>
        <p className="mt-2 text-center text-[14px] text-white/60">Everything stays on this computer — no account, no cloud, no internet. Choose how much it may read; you can change this any time in Settings.</p>
        <div className="mt-6 grid gap-3">
          {ACCESS_CHOICES.map((c) => (
            <button key={c.id} className="flex items-start gap-4 rounded-xl bg-white/[0.04] px-4 py-4 text-left transition-colors hover:bg-white/[0.08] disabled:opacity-50" onClick={() => onChoose(c.id)} disabled={busy}>
              <span className="material-symbols-outlined mt-0.5 text-accent">{c.icon}</span>
              <span>
                <span className="block text-[15px] font-semibold">{c.title}</span>
                <span className="mt-0.5 block text-[13px] text-white/60">{c.body}</span>
              </span>
            </button>
          ))}
        </div>
        <p className="mono mt-5 flex items-center justify-center gap-1 text-[11px] text-white/40"><span className="material-symbols-outlined icon-sm">lock</span>Windows may block protected folders (Controlled Folder Access, other users' folders) — that refusal is Windows', not ours, and is shown on the folder's card.</p>
      </div>
    </div>
  );
}

export function FoldersPage({ status, onError, refresh }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const folders = status?.folders ?? [];
  const access = status?.access;
  const firstRun = status !== null && folders.length === 0;

  async function chooseAccess(mode: Exclude<AccessMode, "unset">) {
    onError(null);
    setBusy("access");
    try {
      const res = await setAccess(mode);
      if (res.error) onError(res.error);
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function addFile() {
    onError(null);
    const selected = await open({ directory: false, multiple: true });
    const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (paths.length === 0) return;
    setBusy("file");
    try {
      for (const p of paths) {
        const res = await indexFile(p);
        if (res.error) onError(res.error);
      }
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (access && access.mode === "unset") {
    return <AccessScreen onChoose={chooseAccess} busy={busy !== null} />;
  }
  if (access && access.mode === "denied") {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="panel max-w-lg p-10 text-center">
          <span className="material-symbols-outlined text-error" style={{ fontSize: 40 }}>block</span>
          <h2 className="mt-3 text-[22px] font-semibold tracking-tight">IntelliFile has no file access</h2>
          <p className="mt-2 text-[14px] text-white/60">Nothing is indexed and search is off. Allow access to start.</p>
          <div className="mt-6 flex justify-center gap-2">
            <button className="btn-gradient px-5 py-2.5 text-[14px] font-medium" onClick={() => chooseAccess("limited")} disabled={busy !== null}>Allow limited</button>
            <button className="btn-ghost px-5 py-2.5 text-[14px]" onClick={() => chooseAccess("all")} disabled={busy !== null}>Allow all</button>
          </div>
        </div>
      </div>
    );
  }

  async function addFolder() {
    onError(null);
    const selected = await open({ directory: true, multiple: false });
    if (typeof selected !== "string") return;
    setBusy(selected);
    try {
      const res = await indexFolder(selected);
      if (res.error) onError(res.error);
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function act(kind: "reindex" | "forget", path: string) {
    onError(null);
    if (kind === "forget") {
      // Removing a folder drops everything indexed under it — the only
      // destructive action in the app, so it asks first (2026-09-21).
      const name = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
      let sure = false;
      try {
        sure = await ask(`Stop watching "${name}" and remove its files from the index?`, { title: "Remove folder", kind: "warning", okLabel: "Remove", cancelLabel: "Keep" });
      } catch {
        sure = window.confirm(`Stop watching "${name}" and remove its files from the index?`); // outside Tauri (browser dev tab)
      }
      if (!sure) return;
    }
    setBusy(path);
    try {
      const res = kind === "reindex" ? await reindexFolder(path) : await forgetFolder(path);
      if ("error" in res && res.error) onError(res.error);
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (firstRun && !status?.access?.whole_computer_roots.length) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="panel max-w-lg p-10 text-center">
          <div className="mx-auto mb-5 grid h-16 w-16 place-items-center rounded-2xl btn-gradient">
            <span className="material-symbols-outlined text-accent" style={{ fontSize: 32 }}>folder_open</span>
          </div>
          <h2 className="text-[22px] font-semibold tracking-tight">Welcome to IntelliFile</h2>
          <p className="mt-2 text-[14px] text-white/60">Choose the folders to search. Everything is indexed and searched on this computer — nothing leaves it.</p>
          <div className="mt-6 flex justify-center gap-2">
            <button className="btn-gradient px-6 py-3 text-[14px] font-medium" onClick={addFolder} disabled={busy !== null}>
              <span className="material-symbols-outlined mr-2 align-middle text-accent">add</span>Add folder
            </button>
            <button className="btn-ghost px-5 py-3 text-[14px]" onClick={addFile} disabled={busy !== null}>
              <span className="material-symbols-outlined mr-2 align-middle icon-sm">note_add</span>Add file
            </button>
          </div>
          <p className="mono mt-5 flex items-center justify-center gap-1 text-[11px] text-white/40">
            <span className="material-symbols-outlined icon-sm">lock</span>100% on-device · no cloud · no account
          </p>
        </div>
      </div>
    );
  }

  const totals = status?.totals;
  const job = status?.job;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="flex items-center gap-3 text-[26px] font-semibold tracking-tight">
            Watched Folders<span className="chip">{folders.length} active</span>
          </h2>
          <p className="mt-1 text-[14px] text-white/55">Folders monitored continuously and indexed locally in the background.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost px-4 py-2 text-[14px]" onClick={addFile} disabled={busy !== null} title="Index a single file without its folder">
            <span className="material-symbols-outlined mr-1.5 align-middle icon-sm">note_add</span>Add file
          </button>
          <button className="btn-gradient px-4 py-2 text-[14px] font-medium" onClick={addFolder} disabled={busy !== null}>
            <span className="material-symbols-outlined mr-1.5 align-middle text-accent">add</span>Add folder
          </button>
        </div>
      </div>
      {access?.mode === "all" && (
        <div className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-[12.5px] text-white/60">
          <span className="material-symbols-outlined icon-sm text-accent">computer</span>
          Whole computer: {access.whole_computer_roots.map(shortenPath).join(", ")} — system folders, app data and caches skipped. Change in Settings → File access.
        </div>
      )}

      {status && totals && (
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Indexed files" value={totals.files.toLocaleString()} icon="database" bar={100} />
          <Stat label="Index size" value={formatBytes(status.index_size_bytes).split(" ")[0]} unit={formatBytes(status.index_size_bytes).split(" ")[1]} icon="storage" bar={Math.min(100, status.index_size_bytes / (500 * 1024 * 1024) * 100)} />
          <Stat label="Watcher" value={job?.state === "running" ? "Indexing" : "Watching"} unit={job?.state === "running" ? `${job.done}/${job.total}` : "live"} icon="sync" bar={job?.state === "running" && job.total ? (job.done / job.total) * 100 : 100} />
        </div>
      )}

      <div className="mono flex items-center justify-between text-[11px] uppercase tracking-wider text-white/45">
        Active monitored paths<span className="normal-case tracking-normal">changes picked up within 30 s</span>
      </div>

      <div className="space-y-2">
        {folders.map((f) => {
          const name = f.path.split(/[\\/]/).filter(Boolean).pop() ?? f.path;
          // A permission refusal under this folder (macOS Privacy & Security, Windows ACLs) — say so on the card.
          const denied = (status?.job.recent_failures ?? []).find((x) => x.error.startsWith("PermissionError") && (x.path === f.path || x.path.startsWith(f.path + "/") || x.path.startsWith(f.path + "\\")));
          const state = f.indexing ? (job?.total ? `Indexing · ${job.done}/${job.total}` : "Indexing…") : f.queued ? "Queued" : f.exists ? "Watching · real-time sync" : "Folder missing";
          return (
            <div key={f.path} className={`panel flex items-center gap-4 px-4 py-3 ${busy === f.path ? "opacity-60" : ""}`}>
              <div className="grid h-12 w-12 place-items-center rounded-xl bg-white/[0.04]">
                <span className={`material-symbols-outlined ${f.indexing ? "text-accent" : "text-white/60"}`}>{f.indexing ? "sync" : f.kind === "file" ? "description" : "folder"}</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[16px] font-semibold">{name}</span>
                  <span className="mono truncate rounded bg-white/[0.04] px-1.5 py-0.5 text-[11px] text-white/45">{shortenPath(f.path)}</span>
                </div>
                <div className="mt-1 text-[13px] text-white/60">
                  {f.documents.toLocaleString()} documents · {f.photos.toLocaleString()} photos · {f.videos.toLocaleString()} videos · <span className={f.audio ? "text-secondary" : ""}>{f.audio.toLocaleString()} audio files{f.audio ? " (transcribed with Whisper)" : ""}</span>
                </div>
                <div className="mono mt-1 flex items-center gap-2 text-[11px]">
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${f.indexing ? "bg-accent animate-pulse" : denied ? "bg-error" : f.exists ? "bg-accent" : "bg-error"}`} />
                  <span className={f.indexing ? "text-accent" : "text-white/55"}>{state}</span>
                </div>
                {denied && <div className="mt-1 text-[12px] text-error">{denied.error.replace(/^PermissionError: /, "")}</div>}
              </div>
              {f.kind !== "file" && <button className="btn-ghost px-3 py-2 text-[13px]" onClick={() => act("reindex", f.path)} disabled={busy !== null || f.indexing}>
                <span className="material-symbols-outlined mr-1 align-middle icon-sm">refresh</span>Re-index
              </button>}
              <button className="btn-ghost px-3 py-2 text-[13px] hover:text-error" onClick={() => act("forget", f.path)} disabled={busy !== null}>
                <span className="material-symbols-outlined mr-1 align-middle icon-sm">delete</span>Remove
              </button>
            </div>
          );
        })}
      </div>

      <div className="panel flex items-start gap-4 px-4 py-4">
        <div className="grid h-12 w-12 place-items-center rounded-xl bg-white/[0.04]"><span className="material-symbols-outlined text-accent">lock</span></div>
        <div>
          <div className="flex items-center gap-2 text-[16px] font-semibold">100% on-device privacy<span className="chip chip-accent">local only</span></div>
          <p className="mt-1 text-[13.5px] text-white/60">Files are indexed in background threads and kept up to date when they change. No telemetry, no cloud calls — the models run on this machine.</p>
        </div>
      </div>
    </div>
  );
}

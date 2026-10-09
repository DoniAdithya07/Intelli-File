import { useEffect, useState } from "react";
import { ask, open } from "@tauri-apps/plugin-dialog";
import { AccessMode, askStatus, engineUnreachable, forgetFolder, getSampleFolder, formatAgo, formatBytes, getRouterStats, indexFile, indexFolder, reindexFolder, RouterStats, scanAll, setAccess, shortenPath, StatusResponse } from "../backend";
import { LoadFailed, PageHeader, Section } from "../ui/kit";
import { FirstRun } from "./FirstRun";

interface Props {
  status: StatusResponse | null;
  onError: (m: string | null) => void;
  refresh: () => Promise<void>;
}

// The four search tiers (docs/UI_DESIGN.md section 11). Reranking is counted, not a tier.
const TIERS: { id: string; label: string; routes: string[] }[] = [
  { id: "filename", label: "File name", routes: ["filename"] },
  { id: "metadata", label: "File details", routes: ["metadata"] },
  { id: "keyword", label: "Keywords", routes: ["keyword"] },
  { id: "hybrid", label: "Keywords + meaning", routes: ["hybrid", "hybrid+rerank"] },
];

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-rule bg-content px-4 py-3">
      <div className="text-[22px] font-semibold leading-tight">{value.toLocaleString()}</div>
      <div className="mt-0.5 text-[12px] text-ink/65">{label}</div>
    </div>
  );
}

export function IndexPage({ status, onError, refresh }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [stats, setStats] = useState<RouterStats | null>(null);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const [askModel, setAskModel] = useState<string | null | undefined>(undefined);
  // Calls the engine answered with an error; when it cannot be reached at all the sidebar says so.
  const [statsFailed, setStatsFailed] = useState(false);
  const [askFailed, setAskFailed] = useState(false);
  useEffect(() => {
    getRouterStats().then(setStats).catch((e) => { setStats(null); setStatsFailed(!engineUnreachable(e)); });
    askStatus().then((s) => setAskModel(s.available ? s.model : null)).catch((e) => setAskFailed(!engineUnreachable(e)));
    getSampleFolder().then((r) => setSample(r.path)).catch(() => setSample(null));
  }, []);
  const [sample, setSample] = useState<string | null>(null);
  const trySample = () => { if (sample) run(sample, async () => { const r = await indexFolder(sample); if (r.error) onError(r.error); }); };

  async function run(key: string, fn: () => Promise<unknown>) {
    onError(null);
    setBusy(key);
    try { await fn(); await refresh(); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  const chooseAccess = (mode: Exclude<AccessMode, "unset">) => run("access", async () => { const r = await setAccess(mode); if (r.error) onError(r.error); });
  // Several folders can be picked at once (Ctrl+click in the Windows folder
  // window). Each is queued and watched; one that is refused does not stop
  // the others, and every refusal is reported by name.
  const addFolder = async () => {
    const selected = await open({ directory: true, multiple: true }).catch(() => null);
    const folders = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (!folders.length) return;
    await run("folders", async () => {
      const problems: string[] = [];
      for (const folder of folders) {
        const r = await indexFolder(folder);
        if (r.error) problems.push(`${folder.split(/[\\/]/).filter(Boolean).pop()}: ${r.error}`);
      }
      if (problems.length) onError(problems.join(" "));
    });
  };
  const addFile = async () => {
    const selected = await open({ directory: false, multiple: true }).catch(() => null);
    const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
    for (const p of paths) await run(p, async () => { const r = await indexFile(p); if (r.error) onError(r.error); });
  };
  const remove = async (path: string) => {
    const name = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
    let sure = false;
    try { sure = await ask(`Stop watching "${name}" and remove its files from the index? The files themselves are not touched.`, { title: "Remove from index", kind: "warning", okLabel: "Remove", cancelLabel: "Keep" }); }
    catch { sure = false; }
    if (sure) await run(path, () => forgetFolder(path));
  };
  const scanNow = () => run("scan", async () => {
    const r = await scanAll();
    if (r.error) onError(r.error);
    else setScanNote(`Checking ${r.folders} folder${r.folders === 1 ? "" : "s"}${r.files ? ` and ${r.files} file${r.files === 1 ? "" : "s"}` : ""} for changes.`);
  });

  if (!status) return <div className="space-y-3"><div className="skeleton h-10 w-60" /><div className="skeleton h-20" /><div className="skeleton h-40" /></div>;
  if (status.access.mode === "unset") {
    return (
      <FirstRun
        busy={busy !== null}
        onChoose={chooseAccess}
        samplePath={sample}
        onTrySample={() => sample && run(sample, async () => {
          const a = await setAccess("limited");
          if (a.error) { onError(a.error); return; }
          const r = await indexFolder(sample);
          if (r.error) onError(r.error);
        })}
      />
    );
  }

  const { job, totals, folders, models } = status;
  const running = job.state === "running";
  const paused = job.paused_reason || status.power.paused_reason;
  const scanFailed = !running && !paused && job.state === "failed";
  const state = paused ? `Paused: ${paused}` : running ? (job.total ? `Indexing ${job.done.toLocaleString()} of ${job.total.toLocaleString()}` : "Indexing") : scanFailed ? `Scan failed: ${job.error || "the reason was not recorded"}` : "Up to date";
  const failures = job.recent_failures ?? [];
  // Left alone on purpose (not failures): one quiet line of totals, and the latest by name.
  const sk = job.skipped ?? {};
  const skippedLine = ([
    [sk.too_large, "over the size limit"],
    [sk.online_only, "online-only OneDrive"],
    [sk.path_too_long, "path too long"],
  ] as const).filter(([n]) => n).map(([n, why]) => `${n!.toLocaleString()} ${why}`).join(", ");
  const skips = job.recent_skips ?? [];

  const searches = (stats?.routes ?? []).filter((r) => r.route !== "agent");
  const searchTotal = searches.reduce((n, r) => n + r.queries, 0);
  const askCount = stats?.routes.find((r) => r.route === "agent")?.queries ?? 0;
  const reranked = searches.find((r) => r.route === "hybrid+rerank")?.queries ?? 0;
  const tierRows = TIERS.map((t) => {
    const rows = searches.filter((r) => t.routes.includes(r.route));
    const n = rows.reduce((a, r) => a + r.queries, 0);
    const ms = n ? rows.reduce((a, r) => a + r.mean_ms * r.queries, 0) / n : 0;
    return { ...t, n, share: searchTotal ? n / searchTotal : 0, ms };
  });

  const modelRows: [string, string | null | undefined][] = [
    ["Text search", models.text?.name ?? null],
    ["Photos", models.photos?.name ?? null],
    ["Speech", models.speech?.name ?? null],
    ["Reranker", models.reranker?.name ?? null],
    ["Text in images (OCR)", models.ocr ? `${models.ocr.name} (${models.ocr.language})` : null],
    ["Ask", askModel],
  ];

  return (
    <div className="h-full space-y-4 overflow-y-auto pr-1">
      <PageHeader title="Index" subtitle="Your files are indexed on this computer and kept up to date as they change.">
        <span role="status" className={`max-w-[360px] rounded-md border px-2.5 py-1 text-[13px] ${scanFailed ? "border-error/40 bg-error-soft text-error" : paused ? "border-rule-strong text-ink/80" : running ? "border-marker text-marker" : "border-rule-strong text-ink/80"}`}>{state}</span>
      </PageHeader>

      {status.access.mode === "denied" && (
        <div className="panel p-4 text-[14px]">
          <b>IntelliFile has no file access.</b> Nothing is indexed and search is off.
          <button className="btn-primary ml-3 px-3 py-1 text-[13px]" onClick={() => chooseAccess("limited")} disabled={busy !== null}>Allow folders I choose</button>
        </div>
      )}

      <div className="grid grid-cols-5 gap-3">
        <Stat label="Files indexed" value={totals.files} />
        <Stat label="Documents" value={totals.documents} />
        <Stat label="Photos" value={totals.photos} />
        <Stat label="Videos" value={totals.videos} />
        <Stat label="Audio" value={totals.audio} />
      </div>

      <div className="panel flex items-center gap-4 p-4">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-medium">Last scan</div>
          <div className="text-[13px] text-ink/65">
            {running && job.current_file ? <>Reading <span className="mono">{job.current_file.split(/[\\/]/).pop()}</span></> : job.finished_at ? `Finished ${formatAgo(job.finished_at)}` : "No scan has finished since IntelliFile started."}
            {scanNote && <> {scanNote}</>}
          </div>
          {skippedLine && <div className="mt-0.5 text-[12px] text-ink/70">Skipped: {skippedLine}.</div>}
          {running && job.total > 0 && <div className="progress mt-2"><i style={{ width: `${(job.done / job.total) * 100}%` }} /></div>}
        </div>
        <span className="text-[12px] text-ink/65">Index size <span className="mono">{formatBytes(status.index_size_bytes)}</span></span>
        <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={scanNow} disabled={busy !== null || status.access.mode === "denied"}>Scan now</button>
      </div>

      <Section title="Indexed folders" note="Changes are picked up within 30 seconds.">
        {folders.length === 0 ? (
          <p className="text-[13px] text-ink/65">Add a folder to start{sample ? ", or try the sample folder: a few notes, invoices and photos that index in under a minute" : ""}.</p>
        ) : (
          <ul className="overflow-hidden rounded-md border border-rule">
            {folders.map((f) => {
              const name = f.path.split(/[\\/]/).filter(Boolean).pop() ?? f.path;
              const st = f.indexing ? "Indexing" : f.queued ? "Queued" : f.exists ? "Watching" : "Missing";
              return (
                <li key={f.path} className={`flex items-center gap-3 border-b border-rule px-3 py-2.5 last:border-b-0 ${busy === f.path ? "opacity-60" : ""}`}>
                  <span className="material-symbols-outlined text-ink/65" aria-hidden>{f.kind === "file" ? "description" : "folder"}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[14px] font-medium">{name}</div>
                    <div className="mono truncate text-[12px] text-ink/65" title={f.path}>{shortenPath(f.path)}</div>
                  </div>
                  <span className="hidden text-[12px] text-ink/65 lg:block">{f.documents} documents, {f.photos} photos, {f.videos} videos, {f.audio} audio</span>
                  <span className={`w-20 text-[12px] ${st === "Missing" ? "text-error" : st === "Watching" ? "text-ink/70" : "text-marker"}`}>{st}</span>
                  {f.kind !== "file" && <button className="btn-ghost px-2 py-1 text-[12px]" onClick={() => run(f.path, () => reindexFolder(f.path))} disabled={busy !== null || f.indexing}>Re-index</button>}
                  <button className="btn-ghost px-2 py-1 text-[12px] hover:text-error" onClick={() => remove(f.path)} disabled={busy !== null}>Remove</button>
                </li>
              );
            })}
          </ul>
        )}
        <div className="mt-3 flex gap-2">
          <button className="btn-primary px-3 py-1.5 text-[13px]" onClick={addFolder} disabled={busy !== null || status.access.mode === "denied"}>Add folder</button>
          <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={addFile} disabled={busy !== null || status.access.mode === "denied"}>Add file</button>
          {sample && !folders.some((f) => f.path === sample) && (
            <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={trySample} disabled={busy !== null || status.access.mode === "denied"}>Try the sample folder</button>
          )}
        </div>
      </Section>

      {failures.length > 0 && (
        <Section title="Files that could not be read" note="IntelliFile skips these and tries again when they change.">
          <ul className="space-y-1 text-[12px]">
            {failures.slice(0, 20).map((f) => (
              <li key={f.path + f.at} className="flex gap-3"><span className="mono min-w-0 flex-1 truncate" title={f.path}>{f.path.split(/[\\/]/).pop()}</span><span className="min-w-0 flex-[2] truncate text-ink/70" title={f.reason || f.error}>{f.reason || f.error}</span></li>
            ))}
          </ul>
        </Section>
      )}

      {skips.length > 0 && (
        <details className="panel group p-4">
          <summary className="flex cursor-default list-none items-baseline gap-2 [&::-webkit-details-marker]:hidden">
            <span className="material-symbols-outlined icon-sm self-center text-ink/70 transition-transform group-open:rotate-90" aria-hidden>chevron_right</span>
            <h2 className="text-[15px] font-semibold">Files skipped</h2>
            <span className="text-[12px] text-ink/70">Left alone on purpose, not errors.</span>
          </summary>
          <ul className="mt-3 space-y-1 text-[12px]">
            {skips.slice(0, 50).map((f) => (
              <li key={f.path} className="flex gap-3"><span className="mono min-w-0 flex-1 truncate" title={f.path}>{f.path.split(/[\\/]/).pop()}</span><span className="min-w-0 flex-[2] truncate text-ink/70">{f.reason}</span></li>
            ))}
          </ul>
        </details>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Section title="How your searches were routed" note={`From the last ${searchTotal.toLocaleString()} remembered searches (up to 500).`}>
          {statsFailed ? <LoadFailed what="the search statistics" /> : !stats || searchTotal === 0 ? (
            <p className="text-[13px] text-ink/65">No remembered searches yet. These statistics need Remember my activity.</p>
          ) : (
            <>
              <ul className="space-y-1.5 text-[12px]">
                {tierRows.map((t) => (
                  <li key={t.id} className="flex items-center gap-3">
                    <span className="w-32 shrink-0 text-ink/80">{t.label}</span>
                    <div className="progress flex-1"><i style={{ width: `${Math.max(t.n ? 3 : 0, t.share * 100)}%` }} /></div>
                    <span className="mono w-10 text-right text-ink/65">{Math.round(t.share * 100)}%</span>
                    <span className="mono w-16 text-right text-ink/65">{t.n ? `${Math.round(t.ms)} ms` : ""}</span>
                  </li>
                ))}
              </ul>
              <dl className="mt-3 space-y-1 border-t border-rule pt-3 text-[12px]">
                <div className="flex justify-between"><dt className="text-ink/70">Reranking</dt><dd>{reranked} search{reranked === 1 ? "" : "es"} needed it</dd></div>
                <div className="flex justify-between"><dt className="text-ink/70">Escalations</dt><dd>{stats.escalated} search{stats.escalated === 1 ? "" : "es"} moved up a tier</dd></div>
                <div className="flex justify-between"><dt className="text-ink/70">Ask questions</dt><dd>{askCount}, counted apart from searches</dd></div>
              </dl>
            </>
          )}
        </Section>
        <Section title="Models on this computer">
          <ul className="space-y-1.5 text-[13px]">
            {modelRows.map(([label, name]) => (
              <li key={label} className="flex items-center gap-3">
                <span className="w-40 shrink-0 text-ink/75">{label}</span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink/65">{name ?? ""}</span>
                <span className={`shrink-0 text-[12px] ${name ? "text-ink/80" : name === null ? "text-error" : "text-ink/65"}`}>{name ? "Installed" : name === null ? "Not installed" : label === "Ask" && askFailed ? "Could not check" : "Checking"}</span>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  );
}

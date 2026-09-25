import { formatAgo, formatBytes, StatusResponse } from "../backend";

interface Props {
  status: StatusResponse | null;
  error: string | null;
}

function Tile({ label, value, note, icon, warn }: { label: string; value: string; note: string; icon: string; warn?: boolean }) {
  return (
    <div className="panel p-4">
      <div className="mono flex items-center justify-between text-[11px] uppercase tracking-wider text-white/45">
        {label}<span className={`grid h-7 w-7 place-items-center rounded-lg bg-white/[0.04] ${warn ? "text-error" : "text-accent"}`}><span className="material-symbols-outlined icon-sm">{icon}</span></span>
      </div>
      <div className={`mt-2 text-[28px] font-semibold leading-none ${warn ? "text-error" : ""}`}>{value}</div>
      <div className="mono mt-2 flex items-center gap-1.5 text-[11px] text-white/45">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${warn ? "bg-error" : "bg-accent"}`} />{note}
      </div>
    </div>
  );
}

function eta(job: StatusResponse["job"]): string | null {
  if (job.state !== "running" || !job.started_at || job.done < 3) return null;
  const rate = job.done / Math.max(1, Date.now() / 1000 - job.started_at); // files per second
  const left = (job.total - job.done) / Math.max(rate, 0.01);
  return left < 60 ? `~${Math.ceil(left)} s` : `~${Math.ceil(left / 60)} min`;
}

export function StatusPage({ status, error }: Props) {
  if (!status) {
    return <div className="flex h-full items-center justify-center text-white/50">{error ? `Backend unreachable: ${error}` : "Loading status…"}</div>;
  }
  const { job, totals, models } = status;
  const running = job.state === "running";
  const pct = running && job.total ? Math.round((job.done / job.total) * 100) : 100;
  const remaining = eta(job);
  const lastFinished = job.finished_at ? formatAgo(job.finished_at) : null;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
      <div className="panel p-5" style={{ background: running ? "linear-gradient(180deg, rgba(94,231,216,.06), transparent 60%), var(--content)" : undefined }}>
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-3 text-[26px] font-semibold tracking-tight">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${running ? "bg-accent animate-pulse" : "bg-accent"}`} />
            {running && job.paused_reason ? <>Indexing paused — {job.paused_reason} <span className="text-accent">{pct}%</span></> : running ? <>Indexing your files… <span className="text-accent">{pct}%</span></> : job.state === "failed" ? "Indexing stopped" : "Everything is up to date"}
          </h2>
          <span className="chip chip-accent">
            <span className="material-symbols-outlined mr-1 align-middle" style={{ fontSize: 13 }}>bolt</span>
            {running && job.paused_reason ? "Held between files · resumes from the same file when power returns" : running ? `Active · running on CPU (${status.power ? `${status.power.app_cpu_percent.toFixed(0)}% of the machine` : "CPU"})` : status.power?.paused ? `Idle · new files will wait: ${status.power.paused_reason}` : "Idle · watching for changes"}
          </span>
        </div>
        <div className="progress mt-4"><i style={{ width: `${pct}%`, animation: running ? undefined : "none" }} /></div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-white/[0.03] p-3">
            <div className="mono text-[11px] uppercase tracking-wider text-white/45">Indexed volume</div>
            <div className="mt-1 text-[15px]">{running ? `${job.done.toLocaleString()} / ${job.total.toLocaleString()} files` : `${totals.files.toLocaleString()} files`}</div>
          </div>
          <div className="rounded-lg bg-white/[0.03] p-3">
            <div className="mono text-[11px] uppercase tracking-wider text-white/45">{running ? "Current file" : "Last run"}</div>
            <div className="mt-1 truncate text-[15px] text-accent">{running ? (job.current_file?.split(/[\\/]/).pop() ?? "…") : lastFinished ? `finished ${lastFinished}` : "—"}</div>
          </div>
          <div className="rounded-lg bg-white/[0.03] p-3">
            <div className="mono text-[11px] uppercase tracking-wider text-white/45">{running ? "Time remaining" : "Queued"}</div>
            <div className="mt-1 text-[15px]">{running ? (remaining ?? "estimating…") : job.queued.length ? `${job.queued.length} folder(s)` : "nothing"}</div>
          </div>
        </div>
        {job.error && <p className="mt-3 text-[13px] text-error">{job.error}</p>}
        {(job.recent_failures?.length ?? 0) > 0 && (
          <details className="mt-3">
            <summary className="mono cursor-pointer text-[11px] uppercase tracking-wider text-white/45">Files that could not be indexed ({job.recent_failures!.length})</summary>
            <div className="mt-2 space-y-1">
              {job.recent_failures!.map((f, i) => (
                <div key={i} className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-1.5 text-[12px]">
                  <span className="truncate text-white/80">{f.path.split(/[\\/]/).pop()}</span>
                  <span className="mono ml-auto shrink-0 truncate text-[11px] text-white/40" title={f.error}>{f.error.split(":")[0]}</span>
                </div>
              ))}
            </div>
          </details>
        )}
        {(job.recovered_at_start ?? 0) > 0 && <p className="mono mt-2 text-[11px] text-white/40">{job.recovered_at_start} file(s) were mid-index at the last exit and were re-indexed on start.</p>}
        <p className="mt-3 flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-[13px] text-white/65">
          <span className="material-symbols-outlined icon-sm text-accent">lightbulb</span>
          You can keep using your computer. Indexing runs in a single background lane so it never competes with what you're doing.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Tile label="Indexed files" value={totals.documents.toLocaleString()} note="Markdown, PDF, DOCX, TXT" icon="description" />
        <Tile label="Photos · videos" value={`${totals.photos.toLocaleString()} · ${totals.videos.toLocaleString()}`} note={models.photos ? `${models.photos.name} ${models.photos.precision}` : "photo model not installed"} icon="image" />
        <Tile label="Audio transcribed" value={totals.audio.toLocaleString()} note={models.speech ? models.speech.name : "speech model not installed"} icon="graphic_eq" />
        <Tile label="Failed / skipped" value={job.failed.toLocaleString()} note={job.failed ? "unreadable in the last run" : "none in the last run"} icon="error" warn={job.failed > 0} />
      </div>

      <div className="panel p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-white/[0.04] text-accent"><span className="material-symbols-outlined">memory</span></span>
            <div>
              <div className="text-[17px] font-semibold">On-device neural stack</div>
              <div className="mono text-[11px] text-white/45">ONNX Runtime · CPU execution provider</div>
            </div>
          </div>
          <span className="mono rounded-lg bg-white/[0.04] px-3 py-1.5 text-[11px] text-white/60">
            Index size: <b className="text-white/85">{formatBytes(status.index_size_bytes)}</b> on disk
          </span>
        </div>
        <div className="mono mt-4 text-[11px] uppercase tracking-wider text-white/45">Active neural weights</div>
        <div className="mt-2 space-y-2">
          {[
            { name: models.text.name, role: "Text semantic vectors", detail: `${models.text.dimension}-dimensional · keyword + meaning fusion`, ok: true },
            { name: models.photos?.name ?? "CLIP", role: "Visual concept mapping", detail: models.photos ? `${models.photos.dimension}-dim · ${models.photos.precision} weights · one image per pass` : "not installed", ok: !!models.photos },
            { name: models.speech?.name ?? "Whisper", role: "Speech to text", detail: models.speech ? "voice search + audio file transcripts" : "not installed", ok: !!models.speech },
          ].map((m) => (
            <div key={m.name} className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-2.5">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/[0.04] text-accent"><span className="material-symbols-outlined icon-sm">deployed_code</span></span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[14px] font-medium">{m.name}<span className="chip chip-accent">{m.role}</span></div>
                <div className="mono mt-0.5 text-[11px] text-white/45">{m.detail}</div>
              </div>
              <span className={`mono text-[11px] ${m.ok ? "text-accent" : "text-error"}`}><span className={`mr-1 inline-block h-1.5 w-1.5 rounded-full ${m.ok ? "bg-accent" : "bg-error"}`} />{m.ok ? "READY" : "MISSING"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

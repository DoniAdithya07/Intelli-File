import { useCallback, useEffect, useState } from "react";
import { AgentManagerStatus, getAgentManager } from "../backend";
import { PageHeader, Section } from "../ui/kit";

function scoreColor(score: number | null): string {
  if (score === null) return "text-ink/50";
  if (score >= 85) return "text-marker";
  if (score >= 60) return "text-ink/80";
  return "text-error";
}

function AgentCard({ agent }: { agent: AgentManagerStatus["agents"][number] }) {
  const statusLabel = agent.status === "running" ? "Running" : agent.status === "degraded" ? "Degraded" : agent.status === "failed" ? "Failed"
    : agent.status === "idle" ? "Idle" : "Not configured";
  const statusClass = agent.status === "running" ? "border-marker text-marker" : agent.status === "failed" ? "border-error/40 text-error"
    : agent.status === "degraded" ? "border-rule-strong text-ink/80" : "border-rule-strong text-ink/60";
  return (
    <div className="panel space-y-2 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-[14px] font-semibold">{agent.name}</h3>
        <span className={`rounded-md border px-2 py-0.5 text-[12px] ${statusClass}`}>{statusLabel}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-[24px] font-semibold ${scoreColor(agent.score)}`}>{agent.score ?? "—"}</span>
        <span className="text-[12px] text-ink/60">/ 100{agent.n ? ` · n=${agent.n}` : ""}</span>
      </div>
      {Object.keys(agent.breakdown).length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] text-ink/65">
          {Object.entries(agent.breakdown).map(([k, v]) => <span key={k}>{k.replace("_", " ")}: {v}</span>)}
        </div>
      )}
      {agent.note && <div className="text-[12px] text-ink/50">{agent.note}</div>}
      {agent.issue && (
        <div className="rounded-md border border-error/30 bg-error-soft p-2 text-[12px] text-error">
          <b>{agent.issue}</b>
          {agent.recovery && <div className="mt-0.5 text-ink/70">{agent.recovery}</div>}
        </div>
      )}
    </div>
  );
}

export function AgentManagerPage({ onError }: { onError: (m: string | null) => void }) {
  const [data, setData] = useState<AgentManagerStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try { setData(await getAgentManager()); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [onError]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
      <PageHeader title="Agent Manager" subtitle="Live health and evidence-based scores for IntelliFile's own search, Ask, photo/video and voice components.">
        <span className="text-[13px] text-ink/70">Overall: <b className={scoreColor(data?.overall_score ?? null)}>{data?.overall_score ?? "—"}</b> / 100</span>
        <button className="btn-secondary px-3 py-1.5 text-[13px]" onClick={load} disabled={busy}>Refresh</button>
      </PageHeader>

      {data?.startup_problems && data.startup_problems.length > 0 && (
        <div className="rounded-md border border-rule-strong bg-field p-3 text-[13px]">
          {data.startup_problems.map((p, i) => <div key={i}>{p}</div>)}
        </div>
      )}

      <Section title="Current issues" note="What, if anything, already happens about each one — no agent here needs a manual restart for these.">
        {!data || data.issues.length === 0 ? (
          <p className="text-[13px] text-ink/65">No issues right now.</p>
        ) : (
          <ul className="space-y-2">
            {data.issues.map((i) => (
              <li key={i.agent} className="rounded-md border border-error/30 bg-error-soft p-2.5 text-[13px]">
                <b>{i.name}:</b> {i.issue}
                {i.recovery && <div className="mt-0.5 text-ink/70">{i.recovery}</div>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Agents" note="Scored from real evaluation files and live status — a missing dimension is left out of the score, never padded.">
        <div className="grid grid-cols-2 gap-3">
          {(data?.agents ?? []).map((a) => <AgentCard key={a.id} agent={a} />)}
        </div>
      </Section>
    </div>
  );
}

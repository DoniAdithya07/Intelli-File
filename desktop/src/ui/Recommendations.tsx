import { useEffect, useState } from "react";
import { getRecommendations, Recommendation, RecommendationsResponse, recordEvent } from "../backend";
import { openResult } from "./ResultCard";

interface Props {
  onError: (m: string | null) => void;
  compact?: boolean;
}

/**
 * Phase 17 — files suggested before any query: what usually follows what
 * is open now, what is usual at this time, and what is used most. Each
 * with its reason; a click opens the file and is remembered as a
 * recommendation_clicked event so the profile learns what worked.
 */
export function Recommendations({ onError, compact }: Props) {
  const [recs, setRecs] = useState<RecommendationsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRecommendations().then((r) => { if (!cancelled) setRecs(r); }).catch(() => { /* nothing to show is fine */ });
    return () => { cancelled = true; };
  }, []);

  if (!recs || !recs.enabled) return null;
  const groups: [string, string, Recommendation[]][] = [
    ["Likely next", "sync_alt", recs.likely_next],
    ["Usual at this time", "schedule", recs.usual_now],
    [recs.cold_start ? "Recently modified" : "Used most", "star", recs.recent],
  ].filter(([, , list]) => list.length > 0) as [string, string, Recommendation[]][];
  if (groups.length === 0) return null;

  function pick(r: Recommendation) {
    recordEvent("recommendation_clicked", { file_id: r.file_id, path: r.path });
    openResult(r.path, onError, r.file_id);
  }

  return (
    <div className={`${compact ? "" : "panel p-4"} w-full`}>
      <div className="mono mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wider text-white/50">
        <span className="material-symbols-outlined icon-sm text-accent">person</span>
        {recs.cold_start ? "While IntelliFile learns your habits" : "Recommended for you"}
        {recs.now && <span className="ml-auto normal-case tracking-normal text-white/35">{recs.now}</span>}
      </div>
      <div className={`grid gap-2 ${compact ? "grid-cols-1" : "grid-cols-3"}`}>
        {groups.map(([title, icon, list]) => (
          <div key={title} className="rounded-lg bg-white/[0.03] p-2.5">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-white/70">
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>{icon}</span>{title}
            </div>
            {list.slice(0, compact ? 3 : 5).map((r) => (
              <button key={r.file_id} className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-white/[0.05]" onClick={() => pick(r)} title={r.reason}>
                <div className="truncate text-[13px] text-white/85">{r.filename}</div>
                <div className="truncate text-[11px] text-white/40">{r.reason}</div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

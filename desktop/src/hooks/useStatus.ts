import { useEffect, useState } from "react";
import { getStatus, StatusResponse } from "../backend";

/** Polls /status — fast while a job runs (the progress bar), slowly otherwise. */
export function useStatus(enabled = true) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await getStatus();
        if (cancelled) return;
        setStatus(s);
        setError(null);
        timer = window.setTimeout(tick, s.job.state === "running" || s.job.queued.length ? 700 : 4000);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(tick, 3000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [enabled]);

  return { status, error, refresh: async () => setStatus(await getStatus()) };
}

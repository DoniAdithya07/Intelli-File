import { Fragment, useCallback, useEffect, useState } from "react";
import { engineUnreachable, formatAgo, getProfile, getRecommendations, getSettings, listEvents, loadSampleHistory, ProfileResponse, Recommendation, RecommendationsResponse, recordEvent, removeSampleHistory, UsageEvent } from "../backend";
import { FileIcon, LoadFailed, PageHeader, Section, shortPlace } from "../ui/kit";
import { openResult } from "../ui/ResultCard";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface Props {
  onError: (m: string | null) => void;
  onSettings: () => void;
}

/** For You (docs/UI_DESIGN.md section 8): every file, reason and number here comes from the local profile. */
export function ForYouPage({ onError, onSettings }: Props) {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [recs, setRecs] = useState<RecommendationsResponse | null>(null);
  const [lastOpened, setLastOpened] = useState<UsageEvent | null | undefined>(undefined);
  const [personalize, setPersonalize] = useState<boolean | null>(null);
  const [failed, setFailed] = useState(false);
  // Sections whose call the engine refused; an engine that is down is the sidebar's to report.
  const [recsFailed, setRecsFailed] = useState(false);
  const [lastFailed, setLastFailed] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);

  const load = useCallback(() => Promise.all([
    getProfile().then(setProfile).catch(() => setFailed(true)),
    getRecommendations().then((r) => { setRecs(r); setRecsFailed(false); }).catch((e) => { setRecs(null); setRecsFailed(!engineUnreachable(e)); }),
    getSettings().then((s) => setPersonalize(s.personalize)).catch(() => setPersonalize(null)),
    listEvents(100).then((r) => { setLastOpened(r.events.find((e) => e.kind === "file_opened" && e.path) ?? null); setLastFailed(false); })
      .catch((e) => { setLastOpened(null); setLastFailed(!engineUnreachable(e)); }),
  ]), []);
  useEffect(() => { load(); }, [load]);

  // Sample history: made-up use of the sample folder, so For You can be seen before real use builds up.
  async function sample(add: boolean) {
    setSampleBusy(true);
    onError(null);
    try {
      if (add) {
        const r = await loadSampleHistory();
        if (r.error) { onError(r.error); return; }
      } else {
        await removeSampleHistory();
      }
      await load();
    } catch (e) {
      onError(`${add ? "Sample history could not be loaded" : "Sample history could not be removed"}. ${e instanceof Error ? e.message : String(e)}`);
    } finally { setSampleBusy(false); }
  }

  const pick = (r: { file_id: string; path: string }) => { recordEvent("recommendation_clicked", { file_id: r.file_id, path: r.path }); openResult(r.path, onError, r.file_id); };

  if (failed) return <div className="p-2 text-[14px] text-ink/70">What IntelliFile learned could not be loaded. The search engine did not answer; open this page again in a moment.</div>;
  if (!profile) return <div className="space-y-3"><div className="skeleton h-10 w-60" /><div className="skeleton h-24" /><div className="skeleton h-40" /></div>;

  if (personalize === false) {
    return (
      <div className="space-y-4">
        <PageHeader title="For You" subtitle="Personalized from your activity" />
        <div className="panel p-5 text-[14px]">
          <b>Personalization is off.</b> Results stay in plain search order and nothing is recommended.
          <button className="btn-secondary ml-3 px-3 py-1 text-[13px]" onClick={onSettings}>Open Settings</button>
        </div>
      </div>
    );
  }

  const recommended: Recommendation[] = recs ? [...recs.likely_next, ...recs.usual_now] : [];
  const uniqueRecs = recommended.filter((r, i) => recommended.findIndex((x) => x.file_id === r.file_id) === i).slice(0, 5);
  const maxHeat = Math.max(1, ...profile.heatmap.flat());
  const slots = profile.heatmap[0]?.length ?? 6;
  const types = Object.entries(profile.type_shares).sort((a, b) => b[1] - a[1]);
  const sampleOffer = !profile.sample_history && (
    <div className="mt-3 flex items-center gap-3">
      <button className="btn-secondary shrink-0 px-3 py-1 text-[13px]" onClick={() => sample(true)} disabled={sampleBusy}>{sampleBusy ? "Loading sample history" : "Load sample history"}</button>
      <span className="text-[12px] text-ink/65">Fills For You with a made-up four weeks of use of the sample folder, so you can see recommendations now. Remove it any time.</span>
    </div>
  );

  return (
    <div className="h-full space-y-4 overflow-y-auto pr-1">
      <PageHeader title="For You" subtitle={`Personalized from your activity: ${profile.events.toLocaleString()} actions in ${profile.sessions} session${profile.sessions === 1 ? "" : "s"}, kept on this computer.`} />

      {profile.sample_history && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-rule-strong bg-content px-4 py-2.5 text-[13px]">
          <span className="material-symbols-outlined icon-sm text-ink/65" aria-hidden>info</span>
          <span className="flex-1"><b>Showing sample history.</b> Some of what is below comes from made-up use of the sample folder, not from you.</span>
          <button className="btn-secondary shrink-0 px-3 py-1 text-[13px]" onClick={() => sample(false)} disabled={sampleBusy}>{sampleBusy ? "Removing" : "Remove sample history"}</button>
        </div>
      )}

      {profile.cold_start && (
        <div className="panel px-4 py-3 text-[13px]">
          <b>Still learning: {profile.events} of {profile.cold_start_threshold}.</b> Searches, opened files, files shown in Explorer and clicked results count. Until then, results stay in plain search order.
          {sampleOffer}
        </div>
      )}

      {lastFailed && <Section title="Continue where you left off"><LoadFailed /></Section>}
      {lastOpened && lastOpened.path && (
        <Section title="Continue where you left off">
          <div className="flex items-center gap-3">
            <FileIcon filename={lastOpened.path} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[14px] font-medium">{lastOpened.path.split(/[\\/]/).pop()}</div>
              <div className="truncate text-[12px] text-ink/65">{shortPlace(lastOpened.path)}</div>
            </div>
            <span className="text-[12px] text-ink/65">Opened {formatAgo(lastOpened.ts)}</span>
            <button className="btn-primary px-3 py-1.5 text-[13px]" onClick={() => openResult(lastOpened.path, onError, lastOpened.file_id)}>Open</button>
          </div>
        </Section>
      )}

      <Section title="Frequently used" note="Opens, reveals and clicks, fading over two weeks.">
        {profile.top_files.length === 0 ? <p className="text-[13px] text-ink/65">Nothing yet.</p> : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-2">
            {profile.top_files.slice(0, 6).map((f) => (
              <button key={f.file_id} className="flex items-center gap-3 rounded-md border border-rule px-3 py-2.5 text-left hover:bg-ink/[0.03]" onClick={() => openResult(f.path, onError, f.file_id)} title={f.path}>
                <FileIcon filename={f.filename} />
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-medium">{f.filename}</span>
                  <span className="block truncate text-[12px] text-ink/65">{f.usual_slot ? `Usually ${f.usual_slot}` : shortPlace(f.path)}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </Section>

      <Section title="Recommended" note={recs?.now ? `For now: ${recs.now}` : undefined}>
        {recsFailed ? <LoadFailed /> : uniqueRecs.length === 0 ? (
          <>
            <p className="text-[13px] text-ink/65">No recommendation yet. They appear once IntelliFile has seen which files you use together or at this time.</p>
            {!profile.cold_start && sampleOffer}
          </>
        ) : (
          <ul className="overflow-hidden rounded-md border border-rule">
            {uniqueRecs.map((r) => (
              <li key={r.file_id} className="border-b border-rule last:border-b-0">
                <button className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-ink/[0.03]" onClick={() => pick(r)}>
                  <FileIcon filename={r.filename} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{r.filename}</span>
                    <span className="block truncate text-[12px] text-ink/65">{shortPlace(r.path)}</span>
                  </span>
                  <span className="max-w-[45%] shrink-0 truncate text-right text-[12px] text-ink/65">{r.reason}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <h2 className="pt-2 text-[15px] font-semibold">What IntelliFile learned</h2>
      <div className="grid grid-cols-2 gap-4">
        <Section title="Topics" note="The files you open, grouped by meaning.">
          {profile.topics.length === 0 ? <p className="text-[13px] text-ink/65">Needs a few opened documents first.</p> : (
            <ul className="space-y-2">
              {profile.topics.map((t) => (
                <li key={t.id}>
                  <div className="flex items-baseline justify-between gap-2 text-[13px]">
                    <span className="font-medium">{t.label}</span>
                    <span className="shrink-0 text-[12px] text-ink/65">{t.file_count} file{t.file_count === 1 ? "" : "s"}</span>
                  </div>
                  <div className="truncate text-[12px] text-ink/65">{t.files.map((f) => f.filename).join(", ")}</div>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="File types you use">
          {types.length === 0 ? <p className="text-[13px] text-ink/65">Nothing yet.</p> : (
            <ul className="space-y-1.5">
              {types.map(([ext, share]) => (
                <li key={ext} className="flex items-center gap-3 text-[12px]">
                  <span className="mono w-12 text-ink/80">{ext.toUpperCase()}</span>
                  <div className="progress flex-1"><i style={{ width: `${Math.max(3, share * 100)}%` }} /></div>
                  <span className="mono w-10 text-right text-ink/65">{Math.round(share * 100)}%</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
      <Section title="When you work" note={`Now: ${profile.now_slot.label}. Darker means more activity.`}>
        <div className="grid gap-1" style={{ gridTemplateColumns: `2.5rem repeat(${slots}, minmax(0, 1fr))` }}>
          <span />
          {Array.from({ length: slots }, (_, i) => (
            <span key={i} className="mono text-center text-[11px] text-ink/65">{String(i * profile.slot_hours).padStart(2, "0")}:00</span>
          ))}
          {profile.heatmap.map((row, d) => (
            <Fragment key={d}>
              <span className="mono self-center text-[12px] text-ink/65">{DAYS[d]}</span>
              {row.map((n, sIdx) => (
                <div
                  key={sIdx}
                  title={`${DAYS[d]} from ${sIdx * profile.slot_hours}:00, ${n} action${n === 1 ? "" : "s"}`}
                  className={`h-6 rounded-[3px] ${d === profile.now_slot.weekday && sIdx === profile.now_slot.slot ? "outline outline-1 outline-ink/60" : ""}`}
                  style={{ background: n ? `rgb(var(--c-marker) / ${0.18 + 0.72 * (n / maxHeat)})` : "rgb(var(--c-ink) / 0.06)" }}
                />
              ))}
            </Fragment>
          ))}
        </div>
      </Section>
    </div>
  );
}

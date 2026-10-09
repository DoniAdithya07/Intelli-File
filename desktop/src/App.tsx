import { useCallback, useEffect, useRef, useState } from "react";
import { backendProblem, checkBackendHealth, setEngineStopped, shortcutOk } from "./backend";
import { useStatus } from "./hooks/useStatus";
import { AskPage } from "./pages/AskPage";
import { ActivityPage } from "./pages/ActivityPage";
import { ForYouPage } from "./pages/ForYouPage";
import { IndexPage } from "./pages/IndexPage";
import { PhotosPage } from "./pages/PhotosPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { HelpPage } from "./pages/HelpPage";
import { AppShell, Tab } from "./shell/AppShell";
import "./App.css";

function App() {
  const [tab, setTab] = useState<Tab>("search");
  // A question handed from Search to Ask (a leading "?" or "Ask instead").
  // Each hand-over is a new object, so Ask runs it exactly once, even when the same words are handed over again.
  const [askQuestion, setAskQuestion] = useState<{ q: string } | null>(null);
  const onAsk = useCallback((q: string) => { setAskQuestion({ q }); setTab("ask"); }, []);
  const [backendStatus, setBackendStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  // The packaged app starts its own engine, which loads ~1.5 GB of models
  // (~30 s). Until it answers — or 90 s pass — that is "starting", not
  // "offline": the first thing the grader sees must not read as broken.
  const launchedAt = useRef(Date.now());
  const [appDataDir, setAppDataDir] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { status, error: statusError, refresh } = useStatus();
  // Why the engine is down, from the desktop shell (port taken, crashed, would not start).
  const [problem, setProblem] = useState<string | null>(null);
  const [shortcut, setShortcut] = useState<boolean | null>(null);
  const [dismissedProblems, setDismissedProblems] = useState(false);
  useEffect(() => { shortcutOk().then(setShortcut); }, []);
  // A page's error belongs to that page.
  useEffect(() => { setError(null); }, [tab]);

  // Keep checking /health: every 2 s until the engine answers (a one-shot
  // check stayed "disconnected" forever, 2026-09-11 audit), then every 5 s,
  // so an engine that crashes later shows as stopped instead of "Works offline".
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let wasConnected = false;
    let misses = 0; // consecutive failed probes once connected: one miss is a blip, not a crash
    const probe = async () => {
      const why = await backendProblem();
      let ok = false;
      if (!why) {
        try {
          const h = await checkBackendHealth();
          ok = true;
          if (!cancelled) setAppDataDir(h.app_data_dir);
        } catch { /* not up, or gone */ }
      }
      if (cancelled) return;
      wasConnected ||= ok;
      misses = ok ? 0 : misses + 1;
      setProblem(why);
      const next = ok ? "connected" : why || (wasConnected && misses >= 2) || Date.now() - launchedAt.current >= 90_000 ? "disconnected" : wasConnected ? "connected" : "checking";
      setEngineStopped(next === "disconnected"); // pages word a failed request the same way as the sidebar
      setBackendStatus(next);
      timer = window.setTimeout(probe, ok ? 5000 : 2000);
    };
    probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  // Phase 12: the first-run question lives on the Index page — go there once
  // when access is found to be unset, but let the user browse the other tabs.
  const askedRef = useRef(false);
  useEffect(() => {
    if (status?.access?.mode === "unset" && !askedRef.current) { askedRef.current = true; setTab("index"); }
  }, [status?.access?.mode]);

  const folderCount = status?.folders.length ?? 0;
  const fileCount = status?.totals.files ?? 0;
  const photoCount = status?.totals.photos ?? 0;
  const videoCount = status?.totals.videos ?? 0;

  return (
    <AppShell
      tab={tab}
      onTab={setTab}
      backendStatus={backendStatus}
      backendProblem={backendStatus === "disconnected" ? problem : null}
      shortcutOk={shortcut !== false}
      notices={dismissedProblems || backendStatus !== "connected" ? [] : status?.startup_problems ?? []}
      onDismissNotices={() => setDismissedProblems(true)}
      fileCount={status ? status.totals.files : null}
      error={error ?? statusError}
      onDismissError={() => setError(null)}
    >
      {/* Search stays mounted so its results survive a visit to another page. */}
      <div className={tab === "search" ? "anim-page h-full" : "hidden"}>
        <SearchPage active={tab === "search"} folderCount={folderCount} fileCount={fileCount} onError={setError} onAsk={onAsk} onSeeAll={() => setTab("foryou")} />
      </div>
      {/* Ask stays mounted too: its session list survives, and a hand-over is answered only once. */}
      <div className={tab === "ask" ? "anim-page h-full overflow-hidden px-6 py-5" : "hidden"}>
        <AskPage request={askQuestion} active={tab === "ask"} onError={setError} />
      </div>
      {tab !== "search" && tab !== "ask" && (
        <div key={tab} className="anim-page h-full overflow-hidden px-6 py-5">
          {tab === "photos" && <PhotosPage photoCount={photoCount} videoCount={videoCount} available={status?.features?.photos_videos !== false} onError={setError} />}
          {tab === "foryou" && <ForYouPage onError={setError} onSettings={() => setTab("settings")} />}
          {tab === "activity" && <ActivityPage onError={setError} />}
          {tab === "index" && <IndexPage status={status} onError={setError} refresh={refresh} />}
          {tab === "settings" && <SettingsPage status={status} appDataDir={appDataDir} onError={setError} refresh={refresh} onOpen={setTab} />}
          {tab === "help" && <HelpPage />}
        </div>
      )}
    </AppShell>
  );
}

export default App;

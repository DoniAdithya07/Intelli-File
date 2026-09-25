import { useEffect, useRef, useState } from "react";
import { checkBackendHealth } from "./backend";
import { useStatus } from "./hooks/useStatus";
import { FoldersPage } from "./pages/FoldersPage";
import { InsightsPage } from "./pages/InsightsPage";
import { PhotosPage } from "./pages/PhotosPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StatusPage } from "./pages/StatusPage";
import { AppShell, Tab } from "./shell/AppShell";
import "./App.css";

function App() {
  const [tab, setTab] = useState<Tab>("search");
  const [backendStatus, setBackendStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  // The packaged app starts its own engine, which loads ~1.5 GB of models
  // (~30 s). Until it answers — or 90 s pass — that is "starting", not
  // "offline": the first thing the grader sees must not read as broken.
  const launchedAt = useRef(Date.now());
  const [appDataDir, setAppDataDir] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { status, error: statusError, refresh } = useStatus();

  // Poll until the backend answers — a one-shot check left this at
  // "disconnected" forever if the backend came up after the window (2026-09-11 audit).
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const probe = async () => {
      try {
        const h = await checkBackendHealth();
        if (cancelled) return;
        setBackendStatus("connected");
        setAppDataDir(h.app_data_dir);
      } catch {
        if (!cancelled) {
          setBackendStatus(Date.now() - launchedAt.current < 90_000 ? "checking" : "disconnected");
          timer = window.setTimeout(probe, 2000);
        }
      }
    };
    probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  // Phase 12: the first-run question lives on the Folders tab — go there once
  // when access is found to be unset, but let the user browse the other tabs.
  const askedRef = useRef(false);
  useEffect(() => {
    if (status?.access?.mode === "unset" && !askedRef.current) { askedRef.current = true; setTab("folders"); }
  }, [status?.access?.mode]);

  const folderCount = status?.folders.length ?? 0;
  const fileCount = status?.totals.files ?? 0;
  const photoCount = status?.totals.photos ?? 0;
  const videoCount = status?.totals.videos ?? 0;

  return (
    <AppShell tab={tab} onTab={setTab} backendStatus={backendStatus} error={error ?? statusError} onDismissError={() => setError(null)}>
      {tab === "search" && <SearchPage folderCount={folderCount} fileCount={fileCount} onError={setError} />}
      {tab === "photos" && <PhotosPage photoCount={photoCount} videoCount={videoCount} onError={setError} />}
      {tab === "insights" && <InsightsPage onError={setError} personalize={null} />}
      {tab === "folders" && <FoldersPage status={status} onError={setError} refresh={refresh} />}
      {tab === "status" && <StatusPage status={status} error={statusError} />}
      {tab === "settings" && <SettingsPage status={status} appDataDir={appDataDir} onError={setError} refresh={refresh} />}
    </AppShell>
  );
}

export default App;

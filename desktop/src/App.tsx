import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowseDrive, ENGINE_STARTING, ENGINE_STOPPED, backendProblem, browseDrives, browseQuickAccess, checkBackendHealth, setEngineStopped, shortcutOk } from "./backend";
import { useStatus } from "./hooks/useStatus";
import { useVoice } from "./hooks/useVoice";
import { baseName, parentOf } from "./lib/format";
import { useFavs } from "./lib/favorites";
import { Location, sameLocation } from "./lib/nav";
import { ActivityPage } from "./pages/ActivityPage";
import { AskPage } from "./pages/AskPage";
import { FolderPage } from "./pages/FolderPage";
import { ForYouPage } from "./pages/ForYouPage";
import { HelpPage } from "./pages/HelpPage";
import { HomePage } from "./pages/HomePage";
import { IndexPage } from "./pages/IndexPage";
import { PhotosPage } from "./pages/PhotosPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ThisPcPage } from "./pages/ThisPcPage";
import { BannerInfo, Shell } from "./shell/Shell";
import { driveName, Pinned } from "./shell/NavTree";
import "./App.css";

/** A true/false choice kept on this computer (the preview pane, per kind of page). */
function usePersisted(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => { try { const s = localStorage.getItem(key); return s === null ? initial : s === "1"; } catch { return initial; } });
  return [v, (n) => { setV(n); try { localStorage.setItem(key, n ? "1" : "0"); } catch { /* not remembered */ } }];
}

const isTyping = (t: EventTarget | null) => { const el = t as HTMLElement | null; return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable); };

function App() {
  const [history, setHistory] = useState<{ stack: Location[]; at: number }>({ stack: [{ kind: "home" }], at: 0 });
  const location = history.stack[history.at];
  const runCounter = useRef(1);
  const nextRun = () => ++runCounter.current;

  const navigate = useCallback((l: Location, opts?: { replace?: boolean }) => {
    setHistory((h) => {
      if (sameLocation(h.stack[h.at], l)) return h;
      const base = h.stack.slice(0, h.at + (opts?.replace ? 0 : 1));
      return { stack: [...base, l], at: base.length };
    });
  }, []);
  const back = () => setHistory((h) => (h.at > 0 ? { ...h, at: h.at - 1 } : h));
  const forward = () => setHistory((h) => (h.at < h.stack.length - 1 ? { ...h, at: h.at + 1 } : h));
  const up = useCallback(() => {
    if (location.kind === "folder") { const p = parentOf(location.path); navigate(p ? { kind: "folder", path: p } : { kind: "thispc" }); }
    else if (location.kind === "search" && location.folder) navigate({ kind: "folder", path: location.folder });
    else if (location.kind !== "home") navigate({ kind: "home" });
  }, [location, navigate]);

  // A question handed from Search to Ask (a leading "?" or "Ask instead"). Each hand-over is a new object, so Ask runs it exactly once.
  const [askQuestion, setAskQuestion] = useState<{ q: string } | null>(null);
  const [askMounted, setAskMounted] = useState(false);
  const onAsk = useCallback((q: string) => { setAskQuestion({ q }); navigate({ kind: "ask" }); }, [navigate]);
  useEffect(() => { if (location.kind === "ask") setAskMounted(true); }, [location.kind]);

  const [backendStatus, setBackendStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  // The packaged app starts its own engine, which loads ~1.5 GB of models (~30 s). Until it answers, or 90 s pass, that is "starting", not "offline".
  const launchedAt = useRef(Date.now());
  const [appDataDir, setAppDataDir] = useState<string | null>(null);
  const [banner, setBanner] = useState<BannerInfo | null>(null);
  const { status, error: statusError, refresh } = useStatus();
  const [problem, setProblem] = useState<string | null>(null);
  const [shortcut, setShortcut] = useState<boolean | null>(null);
  const [dismissedProblems, setDismissedProblems] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [editSignal, setEditSignal] = useState(0);
  useEffect(() => { shortcutOk().then(setShortcut); }, []);

  // One place for "tell the user": a banner under the address row, optionally with one action.
  const notify = useCallback((message: string | null, action?: { label: string; onClick: () => void }) => setBanner(message ? { message, action } : null), []);
  useEffect(() => { setBanner(null); }, [location]);
  // A refused /status is worth saying once the engine is up; while it starts, or when it has stopped, the banners above say so.
  // A failed /status is shown while it lasts, and only when it says something the footer does not.
  // Until 2026-10-13 the first /status of every start (sent before the engine is up) left "The search
  // engine isn't running yet" in a banner: it was shown at the moment the engine connected, and
  // nothing took it away when the next /status worked. "Not running" and "stopped" are the footer's
  // and the engine banner's to say, from the health check; this banner goes when /status works again.
  const statusBanner = useRef<string | null>(null);
  useEffect(() => {
    const connection = statusError === ENGINE_STARTING || statusError === ENGINE_STOPPED;
    if (statusError && !connection && backendStatus === "connected") {
      statusBanner.current = statusError;
      setBanner({ message: statusError });
    } else if (statusBanner.current) {
      const shown = statusBanner.current;
      statusBanner.current = null;
      setBanner((b) => (b?.message === shown ? null : b));
    }
  }, [statusError, backendStatus]);

  // Keep checking /health: every 2 s until the engine answers, then every 5 s, so an engine that crashes later shows as stopped.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let wasConnected = false;
    let misses = 0;
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
      setEngineStopped(next === "disconnected");
      setBackendStatus(next);
      timer = window.setTimeout(probe, ok ? 5000 : 2000);
    };
    probe();
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, []);

  // The first-run question lives on the Index page: go there once when access is found to be unset.
  const askedRef = useRef(false);
  useEffect(() => {
    if (status?.access?.mode === "unset" && !askedRef.current) { askedRef.current = true; navigate({ kind: "index" }); }
  }, [status?.access?.mode, navigate]);

  // Home, This PC and the folder view ask the engine for their lists the moment they open. On a
  // normal start the engine is not up yet (a few seconds, up to about 30 on a cold start), the
  // request failed, and the page said "the search engine isn't running" until the user clicked
  // Try again (found in use, 2026-10-13). They now load again whenever the engine starts answering,
  // also after it was restarted.
  useEffect(() => { if (backendStatus === "connected") setRefreshKey((k) => k + 1); }, [backendStatus]);

  // Drives and the Windows folders for the left tree (the engine lists them; they load when it answers).
  const [drives, setDrives] = useState<BrowseDrive[] | null>(null);
  const [known, setKnown] = useState<Pinned[]>([]);
  const userPins = useFavs("pin");
  useEffect(() => {
    if (backendStatus !== "connected") return;
    browseDrives().then((r) => setDrives(r.drives)).catch(() => setDrives([]));
    browseQuickAccess().then((r) => setKnown(r.items.filter((i) => i.kind === "known").map((i) => ({ name: i.name, path: i.path, user: false })))).catch(() => undefined);
  }, [backendStatus === "connected", refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const pinned = useMemo<Pinned[]>(() => {
    const extra = userPins.filter((p) => !known.some((k) => k.path.toLowerCase() === p.path.toLowerCase())).map((p) => ({ name: p.name, path: p.path, user: true }));
    return [...known, ...extra];
  }, [known, userPins]);
  const driveNames = useMemo(() => Object.fromEntries((drives ?? []).map((d) => [`${d.letter}:`, driveName(d)])), [drives]);

  // ---- the one search box ----
  const [boxText, setBoxText] = useState("");
  const [scope, setScope] = useState<"folder" | "everywhere">("everywhere");
  const [scopeFolder, setScopeFolder] = useState<string | null>(null); // the folder "This folder" refers to
  const [voiceNote, setVoiceNote] = useState<{ forQuery: string; heard: string | null; snap: string | null } | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Where the user is decides what the box searches and what it shows.
  const locKey = JSON.stringify(location.kind === "search" ? { k: "search", f: location.folder ?? null } : location.kind === "photos" ? { k: "photos" } : location);
  useEffect(() => {
    if (location.kind === "folder") { setScopeFolder(location.path); setScope("folder"); setBoxText(""); }
    else if (location.kind === "search") { if (location.folder) { setScopeFolder(location.folder); setScope("folder"); } else setScope("everywhere"); setBoxText(location.query ?? ""); }
    else if (location.kind === "photos") { setScope("everywhere"); setBoxText(location.query ?? ""); }
    else { setScope("everywhere"); setBoxText(""); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locKey]);

  const submit = () => {
    const text = boxText.trim();
    if (!text) return;
    if (text.startsWith("?")) { const q = text.slice(1).trim(); if (q) onAsk(q); return; }
    if (location.kind === "photos") { navigate({ kind: "photos", query: text, run: nextRun() }, { replace: true }); return; }
    const folder = scope === "folder" ? scopeFolder : null;
    navigate({ kind: "search", query: text, folder, run: nextRun() }, { replace: location.kind === "search" });
  };
  const searchFolder = location.kind === "search" ? location.folder ?? null : null;
  const onScope = (s: "folder" | "everywhere") => {
    setScope(s);
    if (location.kind === "search" && location.query) navigate({ kind: "search", query: location.query, folder: s === "folder" ? scopeFolder : null, run: nextRun() }, { replace: true });
  };
  const onEverywhere = () => { if (location.kind === "search") navigate({ kind: "search", query: location.query, folder: null, run: nextRun() }, { replace: true }); };
  const runQuery = useCallback((q: string) => {
    setBoxText(q);
    if (location.kind === "photos") navigate({ kind: "photos", query: q, run: nextRun() }, { replace: true });
    else navigate({ kind: "search", query: q, folder: location.kind === "search" ? location.folder ?? null : null, run: nextRun() }, { replace: location.kind === "search" });
  }, [location, navigate]);
  const clearSearch = useCallback(() => { setBoxText(""); navigate(location.kind === "photos" ? { kind: "photos" } : { kind: "search" }, { replace: true }); }, [location.kind, navigate]);

  const voice = useVoice(
    useCallback((text: string, heard?: string | null, snap?: string | null) => { setBoxText(text); setVoiceNote({ forQuery: text.trim(), heard: heard ?? null, snap: snap ?? null }); }, []),
    useCallback((m: string) => notify(m), [notify]),
  );

  const folderForScope = location.kind === "folder" ? location.path : searchFolder ?? scopeFolder;
  const placeholder = location.kind === "photos" ? "Describe a photo or video" : scope === "folder" && folderForScope ? `Search ${baseName(folderForScope)}` : "Search all your files";
  const scopeChoosable = location.kind === "folder" || (location.kind === "search" && !!scopeFolder);

  // ---- the preview pane: one remembered choice for files and folders, another for results ----
  const [folderPreview, setFolderPreview] = usePersisted("intellifile.preview.folder", false);
  const [resultPreview, setResultPreview] = usePersisted("intellifile.preview.results", true);

  // ---- keyboard map (spec section 5) ----
  const keys = useRef({ back, forward, up, location, navigate, folderPreview, resultPreview });
  keys.current = { back, forward, up, location, navigate, folderPreview, resultPreview };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const k = keys.current;
      const typing = isTyping(e.target);
      if (e.altKey && !e.ctrlKey && e.key === "ArrowLeft") { e.preventDefault(); k.back(); }
      else if (e.altKey && !e.ctrlKey && e.key === "ArrowRight") { e.preventDefault(); k.forward(); }
      else if (e.altKey && !e.ctrlKey && e.key === "ArrowUp") { e.preventDefault(); k.up(); }
      else if (e.altKey && e.key === "Home") { e.preventDefault(); k.navigate({ kind: "home" }); }
      else if ((e.ctrlKey && e.key.toLowerCase() === "l") || (e.altKey && e.key.toLowerCase() === "d") || e.key === "F4") { e.preventDefault(); setEditSignal((n) => n + 1); }
      else if ((e.ctrlKey && (e.key.toLowerCase() === "f" || e.key.toLowerCase() === "e")) || e.key === "F3") { e.preventDefault(); inputRef.current?.focus(); inputRef.current?.select(); }
      else if (e.key === "F5" || (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "r")) {
        e.preventDefault();
        if (k.location.kind === "search" && k.location.query) k.navigate({ ...k.location, run: nextRun() }, { replace: true });
        else if (k.location.kind === "photos" && k.location.query) k.navigate({ ...k.location, run: nextRun() }, { replace: true });
        else setRefreshKey((n) => n + 1);
      }
      else if (e.altKey && e.key.toLowerCase() === "p") { e.preventDefault(); if (k.location.kind === "folder") setFolderPreview(!k.folderPreview); else setResultPreview(!k.resultPreview); }
      else if (e.ctrlKey && e.shiftKey && (e.key === "^" || e.key === "6" || e.key === "@" || e.key === "2")) { e.preventDefault(); window.dispatchEvent(new CustomEvent("intellifile:view", { detail: e.key === "^" || e.key === "6" ? "details" : "icons" })); }
      else if (e.key === "Backspace" && !typing && k.location.kind === "folder") { e.preventDefault(); k.up(); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [setFolderPreview, setResultPreview]);

  // Down in the search box moves into the list without opening anything.
  const onBoxArrow = (dir: 1 | -1) => {
    if (dir !== 1) return;
    const list = document.querySelector<HTMLElement>('main [role="grid"], main [role="listbox"]');
    if (!list) return;
    list.focus();
    list.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  };

  const engineReady = backendStatus === "connected";
  const folderCount = status?.folders.length ?? 0;
  const fileCount = status?.totals.files ?? 0;
  const onIndexChanged = useCallback(() => { refresh().catch(() => undefined); }, [refresh]);
  const onError = useCallback((m: string | null) => notify(m), [notify]);

  return (
    <Shell
      location={location}
      onNavigate={navigate}
      history={{ canBack: history.at > 0, canForward: history.at < history.stack.length - 1, canUp: location.kind !== "home", onBack: back, onForward: forward, onUp: up,
        onRefresh: () => { if ((location.kind === "search" || location.kind === "photos") && location.query) navigate({ ...location, run: nextRun() }, { replace: true }); else setRefreshKey((n) => n + 1); } }}
      editSignal={editSignal}
      backendStatus={backendStatus}
      backendProblem={backendStatus === "disconnected" ? problem : null}
      shortcutOk={shortcut !== false}
      notices={dismissedProblems || backendStatus !== "connected" ? [] : status?.startup_problems ?? []}
      onDismissNotices={() => setDismissedProblems(true)}
      fileCount={status ? status.totals.files : null}
      banner={banner}
      onDismissBanner={() => setBanner(null)}
      pinned={pinned}
      drives={drives}
      driveNames={driveNames}
      box={{ text: boxText, onText: setBoxText, onSubmit: submit, onEscape: () => { if (location.kind === "search" && location.query) clearSearch(); }, scope, onScope, scopeChoosable,
        folderName: folderForScope ? baseName(folderForScope) : "this folder", placeholder, voice, inputRef, onArrow: onBoxArrow }}
    >
      {/* Ask stays mounted once opened: its session list survives, and a hand-over is answered only once. */}
      {askMounted && (
        <div className={location.kind === "ask" ? "anim-page h-full overflow-hidden px-6 py-5" : "hidden"}>
          <AskPage request={askQuestion} active={location.kind === "ask"} onError={onError} />
        </div>
      )}
      {location.kind === "home" && <div key="home" className="anim-page flex min-h-0 flex-1 flex-col"><HomePage status={status} onNavigate={navigate} notify={notify} onIndexChanged={onIndexChanged} refreshKey={refreshKey} /></div>}
      {location.kind === "thispc" && <div key="thispc" className="anim-page flex min-h-0 flex-1 flex-col"><ThisPcPage status={status} onNavigate={navigate} notify={notify} onIndexChanged={onIndexChanged} refreshKey={refreshKey} /></div>}
      {location.kind === "folder" && (
        <div key="folder" className="anim-page flex min-h-0 flex-1 flex-col">
          <FolderPage path={location.path} status={status} nameFilter={scope === "folder" ? boxText : ""} notify={notify} onNavigate={navigate} onIndexChanged={onIndexChanged} refreshKey={refreshKey} preview={folderPreview} onPreview={setFolderPreview} />
        </div>
      )}
      {location.kind === "search" && (
        <div key="search" className="anim-page flex min-h-0 flex-1 flex-col">
          <SearchPage query={location.query ?? ""} folder={location.folder ?? null} run={location.run ?? 0} engineReady={engineReady} folderCount={folderCount} fileCount={fileCount}
            onError={onError} onAsk={onAsk} onSeeAll={() => navigate({ kind: "foryou" })} onRunQuery={runQuery} onEverywhere={onEverywhere} onClear={clearSearch}
            preview={resultPreview} onPreview={setResultPreview} voiceNote={voiceNote} />
        </div>
      )}
      {location.kind === "photos" && (
        <div key="photos" className="anim-page flex min-h-0 flex-1 flex-col">
          <PhotosPage query={location.query ?? ""} run={location.run ?? 0} engineReady={engineReady} photoCount={status?.totals.photos ?? 0} videoCount={status?.totals.videos ?? 0}
            available={status?.features?.photos_videos !== false} notify={notify} onError={onError} onRunQuery={runQuery} preview={resultPreview} onPreview={setResultPreview} />
        </div>
      )}
      {(location.kind === "foryou" || location.kind === "activity" || location.kind === "index" || location.kind === "settings" || location.kind === "help") && (
        <div key={location.kind} className="anim-page min-h-0 flex-1 overflow-hidden px-6 py-5">
          {location.kind === "foryou" && <ForYouPage onError={onError} onSettings={() => navigate({ kind: "settings" })} />}
          {location.kind === "activity" && <ActivityPage onError={onError} />}
          {location.kind === "index" && <IndexPage status={status} onError={onError} refresh={refresh} />}
          {location.kind === "settings" && <SettingsPage status={status} appDataDir={appDataDir} onError={onError} refresh={refresh} onOpen={(p) => navigate({ kind: p })} />}
          {location.kind === "help" && <HelpPage />}
        </div>
      )}
    </Shell>
  );
}

export default App;

import { MutableRefObject, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { BrowseDrive, ENGINE_STOPPED_HINT, ENGINE_STOPPED_TITLE } from "../backend";
import { Location } from "../lib/nav";
import { Logo } from "../Logo";
import { resolvedTheme, useTheme } from "../theme";
import { Banner, Fi, G, ScopeSwitch } from "../ui/controls";
import { Omnibox } from "../ui/Omnibox";
import type { VoiceState } from "../hooks/useVoice";
import { AddressBar } from "./AddressBar";
import { StatusCtx, StatusInfo } from "./ctx";
import { NavTree, Pinned } from "./NavTree";

/** One click between Day and Night; Settings > Appearance also offers "follow Windows". */
function ThemeToggle() {
  const [choice, setChoice] = useTheme();
  const night = resolvedTheme(choice) === "night";
  const label = night ? "Switch to day theme" : "Switch to night theme";
  return (
    <button className="cmd" onClick={() => setChoice(night ? "day" : "night")} title={label} aria-label={label}>
      <Fi g={night ? G.sun : G.moon} />
    </button>
  );
}

export interface SearchBoxProps {
  text: string;
  onText: (v: string) => void;
  onSubmit: () => void;
  onEscape: () => void;
  scope: "folder" | "everywhere";
  onScope: (s: "folder" | "everywhere") => void;
  /** Only in a folder (or its search results) can the scope change. */
  scopeChoosable: boolean;
  folderName: string;
  placeholder: string;
  voice: { state: VoiceState; level: number; toggle: () => void };
  inputRef: MutableRefObject<HTMLInputElement | null>;
  listId?: string;
  searching?: boolean;
  /** Down arrow in the box moves into the list without opening anything. */
  onArrow?: (dir: 1 | -1) => void;
}

export interface BannerInfo { message: string; action?: { label: string; onClick: () => void } }

interface Props {
  location: Location;
  onNavigate: (l: Location) => void;
  history: { canBack: boolean; canForward: boolean; canUp: boolean; onBack: () => void; onForward: () => void; onUp: () => void; onRefresh: () => void };
  editSignal: number;
  backendStatus: "checking" | "connected" | "disconnected";
  backendProblem: string | null;
  shortcutOk: boolean;
  notices: string[];
  onDismissNotices: () => void;
  fileCount: number | null;
  banner: BannerInfo | null;
  onDismissBanner: () => void;
  pinned: Pinned[];
  drives: BrowseDrive[] | null;
  driveNames: Record<string, string>;
  box: SearchBoxProps;
  children: ReactNode;
}

const LEFT = { min: 200, max: 360, def: 240 };
function loadLeft(): number {
  try { const v = Number(localStorage.getItem("intellifile.leftWidth")); if (v >= LEFT.min && v <= LEFT.max) return v; } catch { /* default */ }
  return LEFT.def;
}

/** The window: address row, banners, the left tree, the page, and the status bar. The title bar is Windows' own. */
export function Shell({ location, onNavigate, history, editSignal, backendStatus, backendProblem, shortcutOk, notices, onDismissNotices, fileCount, banner, onDismissBanner, pinned, drives, driveNames, box, children }: Props) {
  const [left, setLeft] = useState(loadLeft);
  const [narrow, setNarrow] = useState(window.innerWidth < 1000);
  const [status, setStatus] = useState<StatusInfo | null>(null);
  const [live, setLive] = useState("");
  const setStatusStable = useCallback((s: StatusInfo | null) => setStatus(s), []);

  useEffect(() => {
    const on = () => setNarrow(window.innerWidth < 1000);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  // The status line is announced once, 300 ms after it stops changing.
  useEffect(() => {
    const t = window.setTimeout(() => setLive(status?.left ?? ""), 300);
    return () => window.clearTimeout(t);
  }, [status?.left]);

  const drag = useRef<{ x: number; w: number } | null>(null);
  const setWidth = (w: number) => {
    const v = Math.max(LEFT.min, Math.min(LEFT.max, w));
    setLeft(v);
    try { localStorage.setItem("intellifile.leftWidth", String(v)); } catch { /* not remembered */ }
  };

  const footer = (
    <div className="shrink-0 border-t border-rule px-3 pb-2 pt-2 text-[12px] leading-snug">
      {narrow ? (
        <div className="flex flex-col items-center gap-1"><ThemeToggle /></div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-2">
            <div role="status" className="min-w-0">
              <div className="font-semibold text-ink">{backendStatus === "connected" ? "Works offline" : backendStatus === "checking" ? "Starting" : ENGINE_STOPPED_TITLE}</div>
              <div className="mt-0.5 text-sub">
                {backendStatus === "connected"
                  ? fileCount === null ? "Your files stay on this computer." : `${fileCount.toLocaleString()} files indexed on this computer`
                  : backendStatus === "checking" ? "Loading the search models. This takes a few seconds." : ENGINE_STOPPED_HINT}
              </div>
            </div>
            <ThemeToggle />
          </div>
          {shortcutOk && <div className="mt-2 text-sub"><span className="kbd">Ctrl+Space</span> quick search</div>}
        </>
      )}
    </div>
  );

  return (
    <StatusCtx.Provider value={setStatusStable}>
      <div className="flex h-screen flex-col overflow-hidden bg-shell text-ink">
        <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
          <div className="hidden shrink-0 items-center gap-2 pr-1 min-[1200px]:flex" aria-hidden><Logo size={20} /></div>
          <div className="flex min-w-[320px] flex-1 items-center">
            <AddressBar location={location} driveNames={driveNames} canBack={history.canBack} canForward={history.canForward} canUp={history.canUp}
              onBack={history.onBack} onForward={history.onForward} onUp={history.onUp} onRefresh={history.onRefresh} onNavigate={onNavigate} editSignal={editSignal} />
          </div>
          <div className="flex min-w-[320px] flex-1 items-center gap-2 min-[1000px]:max-w-[560px] min-[1000px]:flex-none min-[1000px]:basis-[480px]" role="search">
            <div className="min-w-0 flex-1">
              <Omnibox compact value={box.text} onChange={box.onText} onSubmit={box.onSubmit} onEscape={box.onEscape} onArrow={box.onArrow} listId={box.listId} placeholder={box.placeholder}
                searching={box.searching} voice={box.voice} inputRef={box.inputRef} />
            </div>
            {box.scopeChoosable
              ? <ScopeSwitch value={box.scope} folderName={box.folderName} onChange={box.onScope} />
              : <span className="shrink-0 px-1 text-[13px] text-sub" title="Searching all indexed files">Everywhere</span>}
          </div>
        </header>
        {status?.busy && <div className="progress-indeterminate !h-[3px] shrink-0 rounded-none" role="progressbar" aria-label="Working" />}

        {backendStatus === "checking" && !backendProblem && (
          <Banner tone="info" busy>Search is starting. Browsing works now; searching will be ready in a few seconds.</Banner>
        )}
        {backendStatus === "disconnected" && (
          <Banner tone="error" title={ENGINE_STOPPED_TITLE}>{backendProblem ? `${ENGINE_STOPPED_HINT}\n${backendProblem}` : ENGINE_STOPPED_HINT}</Banner>
        )}
        {notices.length > 0 && (
          <Banner tone="warn" title="Some parts did not start." onDismiss={onDismissNotices}>{notices.join(" ")}</Banner>
        )}
        {banner && <Banner tone="error" action={banner.action} onDismiss={onDismissBanner}>{banner.message}</Banner>}

        <div className="flex min-h-0 flex-1">
          <div className="relative flex min-h-0 shrink-0 flex-col bg-nav" style={{ width: narrow ? 48 : left }}>
            <NavTree location={location} onNavigate={onNavigate} pinned={pinned} drives={drives} rail={narrow} footer={footer} />
            {!narrow && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize the folder list"
                aria-valuenow={left}
                aria-valuemin={LEFT.min}
                aria-valuemax={LEFT.max}
                tabIndex={0}
                className="absolute -right-1 top-0 z-20 h-full w-2 cursor-col-resize hover:bg-[var(--hover)]"
                onPointerDown={(e) => { drag.current = { x: e.clientX, w: left }; e.currentTarget.setPointerCapture(e.pointerId); }}
                onPointerMove={(e) => { if (drag.current) setWidth(drag.current.w + e.clientX - drag.current.x); }}
                onPointerUp={() => { drag.current = null; }}
                onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); setWidth(left - 16); } else if (e.key === "ArrowRight") { e.preventDefault(); setWidth(left + 16); } }}
              />
            )}
          </div>
          <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-l border-rule bg-canvas">{children}</main>
        </div>

        <footer className="flex h-6 shrink-0 items-center justify-between gap-4 border-t border-rule bg-status px-3 text-[12px] text-sub">
          <span className="truncate">{status?.left ?? ""}</span>
          <span className="sr-only" role="status" aria-live="polite">{live}</span>
          {status?.view && status.onView && (
            <span className="flex items-center gap-1" role="group" aria-label="View">
              {([["details", G.details, "Details view"], ["icons", G.grid, "Large icons view"]] as const).map(([id, g, label]) => (
                <button key={id} className="cmd !h-5 !min-w-5 !px-1" aria-pressed={status.view === id} aria-label={label} title={label} onClick={() => status.onView!(id)}><Fi g={g} size={14} /></button>
              ))}
            </span>
          )}
        </footer>
      </div>
    </StatusCtx.Provider>
  );
}

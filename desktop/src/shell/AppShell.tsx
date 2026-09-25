import { ReactNode } from "react";
import { Logo } from "../Logo";

export type Tab = "search" | "photos" | "insights" | "folders" | "status" | "settings";

const NAV: { id: Tab; label: string; icon: string }[] = [
  { id: "search", label: "Search", icon: "search" },
  { id: "photos", label: "Photos & Videos", icon: "image" },
  { id: "insights", label: "Insights", icon: "insights" },
  { id: "folders", label: "Folders", icon: "folder" },
  { id: "status", label: "Status", icon: "memory" },
  { id: "settings", label: "Settings", icon: "tune" },
];

interface Props {
  tab: Tab;
  onTab: (t: Tab) => void;
  backendStatus: "checking" | "connected" | "disconnected";
  error: string | null;
  onDismissError: () => void;
  children: ReactNode;
}

/** The persistent window chrome — header, sidebar nav, error banner — around whichever page is active. */
export function AppShell({ tab, onTab, backendStatus, error, onDismissError, children }: Props) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <header className="drag flex h-12 shrink-0 items-center justify-between border-b border-white/[0.07] bg-shell/80 px-4 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <Logo size={26} />
          <span className="text-[15px] font-semibold tracking-tight">IntelliFile</span>
          <span className="chip chip-accent">Local AI</span>
        </div>
        <div className="no-drag flex items-center gap-2">
          <span className="mono flex items-center gap-1.5 rounded-md bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/50">
            <span className="material-symbols-outlined icon-sm">search</span>
            <span className="kbd">Ctrl+Space</span> Quick Search
          </span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[220px] shrink-0 flex-col justify-between border-r border-white/[0.07] bg-shell/60 p-2 backdrop-blur-xl">
          <div className="flex flex-col gap-0.5">
            <div className="mono px-2 py-2 text-[11px] uppercase tracking-wider text-white/40">Workspace</div>
            <nav className="flex flex-col gap-0.5">
              {NAV.map((n) => (
                <button
                  key={n.id}
                  onClick={() => onTab(n.id)}
                  className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px] transition-colors ${
                    tab === n.id ? "bg-white/[0.06] font-semibold text-white" : "text-white/55 hover:bg-white/[0.04] hover:text-white"
                  }`}
                >
                  <span className="material-symbols-outlined icon-sm">{n.icon}</span>
                  {n.label}
                </button>
              ))}
            </nav>
          </div>
          <div className="panel flex flex-col gap-1 p-2.5">
            <div className="flex items-center gap-1.5">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  backendStatus === "connected" ? "bg-accent animate-pulse" : backendStatus === "checking" ? "bg-white/40" : "bg-error"
                }`}
              />
              <span className="mono text-[11px]">
                {backendStatus === "connected" ? "Engine Online" : backendStatus === "checking" ? "Starting the engine…" : "Engine Offline"}
              </span>
            </div>
            <div className="mono text-[10px] leading-tight text-white/45">
              {backendStatus === "checking" ? "loading the local AI models — about 30 s" : backendStatus === "disconnected" ? "quit IntelliFile fully and open it again" : "100% On-Device · MiniLM + CLIP + Whisper"}
            </div>
          </div>
        </aside>

        <div className="flex min-h-0 flex-1 flex-col">
          {error && (
            <div className="mx-4 mt-3 flex items-center gap-2 rounded-lg border border-error/30 bg-error/10 px-3 py-2 text-[13px] text-error">
              <span className="material-symbols-outlined icon-sm">error</span>
              <span className="flex-1">{error}</span>
              <button className="btn-ghost px-2 py-0.5 text-[11px]" onClick={onDismissError}>Dismiss</button>
            </div>
          )}
          <main className="min-h-0 flex-1 overflow-hidden p-4">{children}</main>
        </div>
      </div>
    </div>
  );
}

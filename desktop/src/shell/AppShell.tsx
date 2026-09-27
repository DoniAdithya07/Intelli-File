import { ReactNode, useLayoutEffect, useRef, useState } from "react";
import { Logo } from "../Logo";
import { resolvedTheme, useTheme } from "../theme";

/** One click between Day and Night; Settings > Appearance also offers "follow Windows". */
function ThemeToggle() {
  const [choice, setChoice] = useTheme();
  const night = resolvedTheme(choice) === "night";
  const label = night ? "Switch to day theme" : "Switch to night theme";
  return (
    <button className="btn-ghost grid h-7 w-7 shrink-0 place-items-center" onClick={() => setChoice(night ? "day" : "night")} title={label} aria-label={label}>
      <span className="material-symbols-outlined icon-sm">{night ? "light_mode" : "dark_mode"}</span>
    </button>
  );
}

export type Tab = "search" | "photos" | "ask" | "foryou" | "activity" | "index" | "settings" | "help";

// Three groups, divided by rules (docs/UI_DESIGN.md section 1): finding
// things, what IntelliFile learned, and running it.
const NAV: { id: Tab; label: string; icon: string }[][] = [
  [
    { id: "search", label: "Search", icon: "search" },
    { id: "photos", label: "Photos", icon: "image" },
    { id: "ask", label: "Ask", icon: "chat_bubble" },
  ],
  [
    { id: "foryou", label: "For You", icon: "person" },
    { id: "activity", label: "Activity", icon: "history" },
  ],
  [
    { id: "index", label: "Index", icon: "folder" },
    { id: "settings", label: "Settings", icon: "tune" },
    { id: "help", label: "Help", icon: "help" },
  ],
];

interface Props {
  tab: Tab;
  onTab: (t: Tab) => void;
  backendStatus: "checking" | "connected" | "disconnected";
  fileCount: number | null;
  error: string | null;
  onDismissError: () => void;
  children: ReactNode;
}

/** The window chrome around the active page: the sidebar and the error line. The title bar is Windows' own. */
export function AppShell({ tab, onTab, backendStatus, fileCount, error, onDismissError, children }: Props) {
  // One blue bar that slides to the current page, as in Windows 11's navigation.
  const navRef = useRef<HTMLElement>(null);
  const [barTop, setBarTop] = useState<number | null>(null);
  useLayoutEffect(() => {
    const item = navRef.current?.querySelector<HTMLElement>('[aria-current="page"]');
    setBarTop(item ? item.offsetTop + (item.offsetHeight - 16) / 2 : null);
  }, [tab]);

  return (
    <div className="flex h-screen overflow-hidden bg-shell">
      <aside className="sidebar flex w-[200px] shrink-0 flex-col text-ink">
        <div className="flex items-center gap-2 px-4 pb-2 pt-4">
          <Logo size={26} />
          <span className="text-[16px] font-semibold">IntelliFile</span>
        </div>
        <nav ref={navRef} aria-label="Pages" className="relative flex flex-col px-2 pt-2">
          {barTop !== null && <span className="nav-indicator" style={{ transform: `translateY(${barTop}px)` }} aria-hidden />}
          {NAV.map((group, g) => (
            <div key={g} className={g > 0 ? "mt-2 border-t border-rule pt-2" : ""}>
              {group.map((n) => {
                const active = tab === n.id;
                return (
                  <button
                    key={n.id}
                    onClick={() => onTab(n.id)}
                    aria-current={active ? "page" : undefined}
                    className={`nav-item flex w-full items-center gap-2.5 px-3 py-[7px] text-left text-[14px] ${active ? "text-ink" : "text-ink/80 hover:text-ink"}`}
                  >
                    <span className={`material-symbols-outlined icon-sm ${active ? "text-ink" : "text-ink/65"}`}>{n.icon}</span>
                    {n.label}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="mt-auto border-t border-rule px-3 pb-3 pt-3 text-[12px] leading-snug">
          <div className="flex items-start justify-between gap-2">
            <div role="status" className="min-w-0">
              <div className="font-medium text-ink">
                {backendStatus === "connected" ? "Works offline" : backendStatus === "checking" ? "Starting" : "Search engine stopped"}
              </div>
              <div className="mt-0.5 text-ink/65">
                {backendStatus === "connected"
                  ? fileCount === null ? "Your files stay on this computer." : `${fileCount.toLocaleString()} files indexed on this computer`
                  : backendStatus === "checking"
                    ? "Loading the search models. This takes a few seconds."
                    : "Quit IntelliFile fully and open it again."}
              </div>
            </div>
            <ThemeToggle />
          </div>
          <div className="mt-3 text-ink/65">
            <span className="kbd">Ctrl+Space</span> quick search
          </div>
        </div>
      </aside>

      <div className="app-main flex min-h-0 min-w-0 flex-1 flex-col">
        {error && (
          <div role="alert" className="mx-4 mt-3 flex items-center gap-2 rounded-md border border-error/40 bg-error-soft px-3 py-2 text-[13px] text-error">
            <span className="material-symbols-outlined icon-sm">error</span>
            <span className="flex-1">{error}</span>
            <button className="btn-ghost px-2 py-0.5 text-[12px]" onClick={onDismissError}>Dismiss</button>
          </div>
        )}
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}

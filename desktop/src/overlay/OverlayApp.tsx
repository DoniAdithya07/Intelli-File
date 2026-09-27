import { useCallback, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useStatus } from "../hooks/useStatus";
import { OverlaySearch } from "./OverlaySearch";
import "../App.css";

/**
 * Ctrl+Space popup — SearchPage in compact mode inside the overlay window
 * (transparent/frameless/always-on-top, declared in src-tauri/tauri.conf.json).
 * The Rust side toggles this window's visibility and emits "overlay-shown" on
 * each open so the box resets instead of showing the last search.
 */
export function OverlayApp() {
  const [resetKey, setResetKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const { status } = useStatus();

  useEffect(() => {
    const unlisten = listen("overlay-shown", () => {
      setError(null);
      setResetKey((k) => k + 1);
    });
    return () => {
      unlisten.then((f) => f());
    };
  }, []);

  const hide = useCallback(() => {
    getCurrentWindow().hide();
  }, []);

  return (
    <div className="flex h-screen flex-col p-3">
      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-rule shadow-palette"
        style={{ background: "var(--overlay)" }}
      >
        <div className="min-h-0 flex-1 p-3">
          <OverlaySearch
            key={resetKey}
            folderCount={status?.folders.length ?? 0}
            fileCount={status?.totals.files ?? 0}
            onError={setError}
            compact
            onEscape={hide}
          />
        </div>
        {error && <div className="shrink-0 border-t border-rule px-3 py-2 text-[12px] text-error">{error}</div>}
      </div>
    </div>
  );
}

import { createContext, useContext, useEffect } from "react";

/** What a page tells the status bar: the count line, whether it is working, and the view switch. */
export interface StatusInfo {
  left: string;
  busy?: boolean;
  view?: "details" | "list" | "icons";
  onView?: (v: "details" | "list" | "icons") => void;
}
export const StatusCtx = createContext<(s: StatusInfo | null) => void>(() => undefined);

export function useStatusBar(info: StatusInfo, active = true) {
  const set = useContext(StatusCtx);
  useEffect(() => {
    if (!active) return;
    set(info);
    return () => set(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.left, info.busy, info.view, info.onView, active, set]);
}

import { CSSProperties, KeyboardEvent, MouseEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

export interface GridItem<T> { key: string; data: T }

interface Props<T> {
  label: string;
  items: GridItem<T>[];
  /** CSS grid column rule, e.g. "repeat(auto-fill, 160px)". */
  columns: string;
  gap?: number;
  renderTile: (row: T) => ReactNode;
  tileLabel: (row: T) => string;
  getName: (row: T) => string;
  onOpen: (row: T) => void;
  onReveal?: (row: T) => void;
  onCopyPaths?: (rows: T[]) => void;
  onContext?: (rows: T[], x: number, y: number) => void;
  onSelection?: (rows: T[]) => void;
  tileStyle?: CSSProperties;
}

/** Tiles in a grid (Photos, Large icons, List). One tab stop; the arrow keys move in two dimensions. */
export function FileGrid<T>({ label, items, columns, gap = 12, renderTile, tileLabel, getName, onOpen, onReveal, onCopyPaths, onContext, onSelection, tileStyle }: Props<T>) {
  const box = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const anchor = useRef<string | null>(null);
  const typed = useRef({ text: "", at: 0 });
  const indexOf = useMemo(() => new Map(items.map((it, i) => [it.key, i])), [items]);

  useEffect(() => {
    setSelected((s) => { const keep = new Set([...s].filter((k) => indexOf.has(k))); return keep.size === s.size ? s : keep; });
    setFocusKey((f) => (f && indexOf.has(f) ? f : null));
  }, [indexOf]);
  const selectedRows = useMemo(() => items.filter((r) => selected.has(r.key)).map((r) => r.data), [items, selected]);
  const cb = useRef(onSelection);
  cb.current = onSelection;
  const selKey = useMemo(() => [...selected].join("|"), [selected]);
  const lastSel = useRef("");
  useEffect(() => {
    if (lastSel.current === selKey) return;
    lastSel.current = selKey;
    cb.current?.(selectedRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey]);

  const pick = (i: number, mode: "one" | "extend" | "focus" | "toggle") => {
    i = Math.max(0, Math.min(items.length - 1, i));
    const it = items[i];
    if (!it) return;
    setFocusKey(it.key);
    if (mode === "one") { setSelected(new Set([it.key])); anchor.current = it.key; }
    else if (mode === "toggle") { setSelected((s) => { const n = new Set(s); if (n.has(it.key)) n.delete(it.key); else n.add(it.key); return n; }); anchor.current = it.key; }
    else if (mode === "extend") {
      const a = anchor.current !== null ? indexOf.get(anchor.current) ?? 0 : 0;
      const [lo, hi] = a < i ? [a, i] : [i, a];
      setSelected(new Set(items.slice(lo, hi + 1).map((x) => x.key)));
    }
    box.current?.querySelector(`[data-key="${CSS.escape(it.key)}"]`)?.scrollIntoView({ block: "nearest" });
  };

  const perRow = () => {
    const tiles = [...(box.current?.querySelectorAll<HTMLElement>("[data-key]") ?? [])];
    if (!tiles.length) return 1;
    const top = tiles[0].offsetTop;
    return Math.max(1, tiles.findIndex((t) => t.offsetTop !== top) === -1 ? tiles.length : tiles.findIndex((t) => t.offsetTop !== top));
  };
  const focusIndex = focusKey !== null ? indexOf.get(focusKey) ?? -1 : -1;

  function onKeyDown(e: KeyboardEvent) {
    const i = focusIndex < 0 ? 0 : focusIndex;
    const ctrl = e.ctrlKey || e.metaKey;
    const move = (to: number) => { e.preventDefault(); pick(to, e.shiftKey ? "extend" : ctrl ? "focus" : "one"); };
    switch (e.key) {
      case "ArrowRight": return move(focusIndex < 0 ? 0 : i + 1);
      case "ArrowLeft": return move(focusIndex < 0 ? 0 : i - 1);
      case "ArrowDown": return move(focusIndex < 0 ? 0 : i + perRow());
      case "ArrowUp": return move(focusIndex < 0 ? 0 : i - perRow());
      case "Home": return move(0);
      case "End": return move(items.length - 1);
      case "PageDown": return move(i + perRow() * 3);
      case "PageUp": return move(i - perRow() * 3);
      case " ": if (focusIndex >= 0) { e.preventDefault(); pick(i, "toggle"); } return;
      case "Escape": if (selected.size) { e.preventDefault(); setSelected(new Set()); } return;
      case "Enter": if (focusIndex >= 0) { e.preventDefault(); if (ctrl) onReveal?.(items[i].data); else onOpen(items[i].data); } return;
      case "ContextMenu": case "F10": {
        if ((e.key === "F10" && !e.shiftKey) || focusIndex < 0) return;
        e.preventDefault();
        const r = box.current?.querySelector(`[data-key="${CSS.escape(items[i].key)}"]`)?.getBoundingClientRect();
        if (!selected.has(items[i].key)) setSelected(new Set([items[i].key]));
        onContext?.(selected.has(items[i].key) ? selectedRows : [items[i].data], (r?.left ?? 0) + 20, (r?.bottom ?? 0) - 20);
        return;
      }
    }
    if (ctrl && e.key.toLowerCase() === "a") { e.preventDefault(); setSelected(new Set(items.map((r) => r.key))); return; }
    if (ctrl && e.shiftKey && e.key.toLowerCase() === "c") { e.preventDefault(); if (selectedRows.length) onCopyPaths?.(selectedRows); return; }
    if (e.key.length === 1 && !ctrl && !e.altKey) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at > 800 ? "" : typed.current.text) + e.key.toLowerCase(), at: now };
      const hit = items.findIndex((r) => getName(r.data).toLowerCase().startsWith(typed.current.text));
      if (hit >= 0) { e.preventDefault(); pick(hit, "one"); }
    }
  }
  const down = (e: MouseEvent, key: string) => {
    if (e.button === 2) return;
    box.current?.focus({ preventScroll: true });
    pick(indexOf.get(key)!, e.shiftKey ? "extend" : e.ctrlKey || e.metaKey ? "toggle" : "one");
  };
  const ctx = (e: MouseEvent, key: string) => {
    e.preventDefault();
    const row = items[indexOf.get(key)!].data;
    if (!selected.has(key)) { setSelected(new Set([key])); setFocusKey(key); anchor.current = key; }
    onContext?.(selected.has(key) ? selectedRows : [row], e.clientX, e.clientY);
  };

  return (
    <div
      ref={box}
      role="listbox"
      aria-label={label}
      aria-multiselectable
      aria-activedescendant={focusKey ? `tile-${indexOf.get(focusKey)}` : undefined}
      tabIndex={0}
      className="list-wrap min-h-0 flex-1 overflow-auto p-3"
      onKeyDown={onKeyDown}
      onMouseDown={(e) => { if (e.target === e.currentTarget) setSelected(new Set()); }}
      onContextMenu={(e) => { if (e.target === e.currentTarget) { e.preventDefault(); setSelected(new Set()); onContext?.([], e.clientX, e.clientY); } }}
    >
      <div className="grid justify-start" style={{ gridTemplateColumns: columns, gap }}>
        {items.map((it, i) => (
          <div
            key={it.key}
            id={`tile-${i}`}
            data-key={it.key}
            data-focus={focusKey === it.key}
            role="option"
            aria-selected={selected.has(it.key)}
            aria-label={tileLabel(it.data)}
            className="tile cursor-default p-1"
            style={{ contentVisibility: "auto", containIntrinsicSize: "auto 200px", ...tileStyle }}
            onMouseDown={(e) => down(e, it.key)}
            onDoubleClick={() => onOpen(it.data)}
            onContextMenu={(e) => ctx(e, it.key)}
          >
            {renderTile(it.data)}
          </div>
        ))}
      </div>
    </div>
  );
}

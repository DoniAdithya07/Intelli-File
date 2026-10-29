import { KeyboardEvent, MouseEvent, ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Fi, G } from "./controls";

export interface Column<T> { id: string; label: string; width: string; align?: "right"; sortable?: boolean; render: (row: T) => ReactNode }
export type Item<T> = { key: string; data: T } | { key: string; group: string; open: boolean; onToggle: () => void };
export interface Sort { col: string; dir: "asc" | "desc" }

const HEADER_H = 32;
const GROUP_H = 36;
const isRow = <T,>(i: Item<T>): i is { key: string; data: T } => "data" in i;

interface Props<T> {
  label: string;
  columns: Column<T>[];
  items: Item<T>[];
  rowHeight: number;
  sort?: Sort;
  onSort?: (col: string) => void;
  getName: (row: T) => string;
  onOpen: (row: T) => void;
  onReveal?: (row: T) => void;
  onCopyPaths?: (rows: T[]) => void;
  onContext?: (rows: T[], x: number, y: number) => void;
  onSelection?: (rows: T[]) => void;
  className?: string;
}

/**
 * Explorer's details list: a header you can sort by, rows you can select with the mouse or the keyboard,
 * and only the rows on screen in the page, so a folder of 5,000 files scrolls as smoothly as one of 5.
 * The list is one tab stop; the arrow keys move inside it (aria-activedescendant).
 */
export function FileTable<T>({ label, columns, items, rowHeight, sort, onSort, getName, onOpen, onReveal, onCopyPaths, onContext, onSelection, className = "" }: Props<T>) {
  const box = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const anchor = useRef<string | null>(null);
  const typed = useRef({ text: "", at: 0 });

  // Where each item sits: rows have one height, group headers another.
  const { tops, total, rows, indexOf } = useMemo(() => {
    const tops: number[] = [];
    let y = HEADER_H;
    const rows: { key: string; data: T }[] = [];
    const indexOf = new Map<string, number>();
    items.forEach((it) => {
      tops.push(y);
      y += isRow(it) ? rowHeight : GROUP_H;
      if (isRow(it)) { indexOf.set(it.key, rows.length); rows.push(it); }
    });
    return { tops, total: y, rows, indexOf };
  }, [items, rowHeight]);

  // Keep only selected rows that still exist (a new sort or filter keeps the rest).
  useEffect(() => {
    setSelected((s) => {
      const keep = new Set([...s].filter((k) => indexOf.has(k)));
      return keep.size === s.size ? s : keep;
    });
    setFocusKey((f) => (f && indexOf.has(f) ? f : null));
  }, [indexOf]);

  const selectedRows = useMemo(() => rows.filter((r) => selected.has(r.key)).map((r) => r.data), [rows, selected]);
  // Tell the page when the selection changes (not on every render, or a page that keeps it in state would loop).
  const onSelectionRef = useRef(onSelection);
  onSelectionRef.current = onSelection;
  const selKey = useMemo(() => [...selected].join("|"), [selected]);
  const lastSel = useRef("");
  useEffect(() => {
    if (lastSel.current === selKey) return;
    lastSel.current = selKey;
    onSelectionRef.current?.(selectedRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight));
    ro.observe(el);
    setViewH(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const scrollTo = useCallback((key: string) => {
    const el = box.current;
    const at = items.findIndex((i) => i.key === key);
    if (!el || at < 0) return;
    const top = tops[at], h = isRow(items[at]) ? rowHeight : GROUP_H;
    if (top - HEADER_H < el.scrollTop) el.scrollTop = top - HEADER_H;
    else if (top + h > el.scrollTop + el.clientHeight) el.scrollTop = top + h - el.clientHeight;
  }, [items, tops, rowHeight]);

  const pick = useCallback((i: number, mode: "one" | "extend" | "focus" | "toggle") => {
    i = Math.max(0, Math.min(rows.length - 1, i));
    const r = rows[i];
    if (!r) return;
    setFocusKey(r.key);
    if (mode === "one") { setSelected(new Set([r.key])); anchor.current = r.key; }
    else if (mode === "toggle") { setSelected((s) => { const n = new Set(s); if (n.has(r.key)) n.delete(r.key); else n.add(r.key); return n; }); anchor.current = r.key; }
    else if (mode === "extend") {
      const a = anchor.current !== null ? indexOf.get(anchor.current) ?? 0 : 0;
      const [lo, hi] = a < i ? [a, i] : [i, a];
      setSelected(new Set(rows.slice(lo, hi + 1).map((x) => x.key)));
    }
    scrollTo(r.key);
  }, [rows, indexOf, scrollTo]);

  const focusIndex = focusKey !== null ? indexOf.get(focusKey) ?? -1 : -1;
  const page = Math.max(1, Math.floor((viewH - HEADER_H) / rowHeight) - 1);

  function onKeyDown(e: KeyboardEvent) {
    if ((e.target as HTMLElement).closest("button") && e.target !== e.currentTarget) return; // a header button handles its own keys
    const i = focusIndex < 0 ? 0 : focusIndex;
    const ctrl = e.ctrlKey || e.metaKey;
    const move = (to: number) => { e.preventDefault(); pick(to, e.shiftKey ? "extend" : ctrl ? "focus" : "one"); };
    switch (e.key) {
      case "ArrowDown": return move(focusIndex < 0 ? 0 : i + 1);
      case "ArrowUp": return move(focusIndex < 0 ? 0 : i - 1);
      case "Home": return move(0);
      case "End": return move(rows.length - 1);
      case "PageDown": return move(i + page);
      case "PageUp": return move(i - page);
      case " ": if (focusIndex >= 0) { e.preventDefault(); pick(i, "toggle"); } return;
      case "Escape": if (selected.size) { e.preventDefault(); setSelected(new Set()); } return;
      case "Enter": {
        if (focusIndex < 0) return;
        e.preventDefault();
        if (ctrl) onReveal?.(rows[i].data); else onOpen(rows[i].data);
        return;
      }
      case "ContextMenu": case "F10": {
        if (e.key === "F10" && !e.shiftKey) return;
        if (focusIndex < 0) return;
        e.preventDefault();
        const rect = box.current?.querySelector(`[data-key="${CSS.escape(rows[i].key)}"]`)?.getBoundingClientRect();
        const chosen = selected.has(rows[i].key) ? selectedRows : [rows[i].data];
        if (!selected.has(rows[i].key)) setSelected(new Set([rows[i].key]));
        onContext?.(chosen, (rect?.left ?? 0) + 40, (rect?.bottom ?? 0));
        return;
      }
    }
    if (ctrl && e.key.toLowerCase() === "a") { e.preventDefault(); setSelected(new Set(rows.map((r) => r.key))); return; }
    if (ctrl && e.shiftKey && e.key.toLowerCase() === "c") { e.preventDefault(); if (selectedRows.length) onCopyPaths?.(selectedRows); return; }
    // Type a few letters to jump to a name.
    if (e.key.length === 1 && !ctrl && !e.altKey) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at > 800 ? "" : typed.current.text) + e.key.toLowerCase(), at: now };
      const hit = rows.findIndex((r) => getName(r.data).toLowerCase().startsWith(typed.current.text));
      if (hit >= 0) { e.preventDefault(); pick(hit, "one"); }
    }
  }

  function onRowDown(e: MouseEvent, key: string) {
    if (e.button === 2) return;
    const i = indexOf.get(key)!;
    box.current?.focus({ preventScroll: true });
    pick(i, e.shiftKey ? "extend" : e.ctrlKey || e.metaKey ? "toggle" : "one");
  }
  function onRowContext(e: MouseEvent, key: string) {
    e.preventDefault();
    const chosen = selected.has(key) ? selectedRows : [rows[indexOf.get(key)!].data];
    if (!selected.has(key)) { setSelected(new Set([key])); setFocusKey(key); anchor.current = key; }
    onContext?.(chosen, e.clientX, e.clientY);
  }

  // The rows on screen, with a little either side.
  const first = Math.max(0, bsearch(tops, scrollTop - HEADER_H) - 4);
  const last = Math.min(items.length, bsearch(tops, scrollTop + viewH) + 4);
  const template = columns.map((c) => c.width).join(" ");

  return (
    <div
      ref={box}
      role="grid"
      aria-label={label}
      aria-multiselectable
      aria-rowcount={rows.length}
      aria-activedescendant={focusKey ? `row-${hash(focusKey)}` : undefined}
      tabIndex={0}
      className={`list-wrap relative min-h-0 flex-1 overflow-auto ${className}`}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      onKeyDown={onKeyDown}
      onMouseDown={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).dataset.empty) setSelected(new Set()); }}
      onContextMenu={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).dataset.empty) { e.preventDefault(); setSelected(new Set()); onContext?.([], e.clientX, e.clientY); } }}
    >
      <div style={{ height: total, minWidth: 640, position: "relative" }} data-empty="1">
        <div role="row" className="sticky top-0 z-10 grid items-center border-b border-rule bg-content text-[12px] text-dim" style={{ gridTemplateColumns: template, height: HEADER_H }}>
          {columns.map((c) => {
            const active = sort?.col === c.id;
            return (
              <div key={c.id} role="columnheader" aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : c.sortable ? "none" : undefined} className={`min-w-0 ${c.align === "right" ? "text-right" : ""}`}>
                {c.sortable && onSort ? (
                  <button className="flex h-8 w-full items-center gap-1 px-2 hover:bg-[var(--hover)] focus-visible:outline-offset-[-2px]" style={{ justifyContent: c.align === "right" ? "flex-end" : "flex-start" }} onClick={() => onSort(c.id)}>
                    <span className="truncate">{c.label}</span>
                    {active && <Fi g={sort!.dir === "asc" ? G.up : G.down} size={10} />}
                  </button>
                ) : <span className="block px-2">{c.label}</span>}
              </div>
            );
          })}
        </div>
        {items.slice(first, last).map((it, k) => {
          const at = first + k;
          if (!isRow(it)) {
            return (
              <div key={it.key} role="row" style={{ position: "absolute", top: tops[at], left: 0, right: 0, height: GROUP_H }}>
                <div role="gridcell" aria-colspan={columns.length} className="flex h-full items-center px-2">
                  <button className="flex h-8 items-center gap-2 rounded px-2 text-[14px] font-semibold hover:bg-[var(--hover)]" aria-expanded={it.open} onClick={it.onToggle}>
                    <Fi g={it.open ? G.down : G.right} size={12} />{it.group}
                  </button>
                </div>
              </div>
            );
          }
          const rowIndex = indexOf.get(it.key)!;
          const isSel = selected.has(it.key);
          return (
            <div
              key={it.key}
              id={`row-${hash(it.key)}`}
              data-key={it.key}
              data-focus={focusKey === it.key}
              role="row"
              aria-rowindex={rowIndex + 1}
              aria-selected={isSel}
              className="row grid items-center"
              style={{ position: "absolute", top: tops[at], left: 0, right: 0, height: rowHeight, gridTemplateColumns: template }}
              onMouseDown={(e) => onRowDown(e, it.key)}
              onDoubleClick={() => onOpen(it.data)}
              onContextMenu={(e) => onRowContext(e, it.key)}
            >
              {columns.map((c) => (
                <div key={c.id} role="gridcell" className={`min-w-0 px-2 text-[14px] ${c.align === "right" ? "text-right" : ""}`}>{c.render(it.data)}</div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Index of the first top that is greater than y (the items before it start above y). */
function bsearch(tops: number[], y: number): number {
  let lo = 0, hi = tops.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (tops[mid] <= y) lo = mid + 1; else hi = mid; }
  return Math.max(0, lo - 1);
}
function hash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

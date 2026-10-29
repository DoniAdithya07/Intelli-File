import { KeyboardEvent, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

/** A Segoe Fluent Icons glyph (Windows' own icon font). `g` is the code point. */
export const G = {
  home: "E80F", back: "E72B", forward: "E72A", up: "E74A", refresh: "E72C", search: "E721", folder: "E8B7", picture: "E91B", chat: "E8BD", history: "E81C",
  settings: "E713", help: "E897", sort: "E8CB", filter: "E71C", more: "E712", right: "E76C", down: "E70D", pin: "E718", star: "E734", drive: "EDA2", mic: "E720",
  cancel: "E711", play: "E768", person: "E77B", sun: "E706", moon: "E708", view: "E8A9", pane: "E89F", copy: "E8C8", newFolder: "E8F4", check: "E73E",
  info: "E946", warn: "E7BA", error: "EA39", thispc: "E7F4", doc: "E8A5", list: "EA37", grid: "F0E2", details: "E8FD", sync: "E895",
} as const;

export function Fi({ g, size = 16, className = "" }: { g: string; size?: number; className?: string }) {
  return <i className={`fi ${className}`} style={{ fontSize: size, width: size, height: size }} aria-hidden>{String.fromCodePoint(parseInt(g, 16))}</i>;
}

/** Explorer's yellow folder (no emoji, no gradient). */
export function FolderIcon({ size = 20, dim }: { size?: number; dim?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className="shrink-0" style={dim ? { opacity: 0.5 } : undefined}>
      <path d="M2 6.5A1.5 1.5 0 0 1 3.5 5h5.2c.4 0 .8.2 1.1.5L11 7h9.5A1.5 1.5 0 0 1 22 8.5V9H2z" fill="#F0A30A" />
      <path d="M2 9h20v9.5a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 18.5z" fill="#FFC83D" />
    </svg>
  );
}

/** Rounded choice pills (Recent / Favorites, All / Documents ...). One tab stop; Left and Right move. */
export function Pills<T extends string>({ items, value, onChange, label }: { items: { id: T; label: string; icon?: string; count?: number }[]; value: T; onChange: (v: T) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (i + d + items.length) % items.length;
    onChange(items[n].id);
    refs.current[n]?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-2">
      {items.map((p, i) => (
        <button
          key={p.id}
          ref={(el) => { refs.current[i] = el; }}
          role="tab"
          aria-selected={value === p.id}
          tabIndex={value === p.id ? 0 : -1}
          className="pill text-[14px]"
          onClick={() => onChange(p.id)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {p.icon && <Fi g={p.icon} />}
          {p.label}{p.count !== undefined ? ` ${p.count}` : ""}
        </button>
      ))}
    </div>
  );
}

/** A full-width line under the command bar. */
export function Banner({ tone, title, children, action, onDismiss, busy }: { tone: "info" | "warn" | "error"; title?: string; children?: ReactNode; action?: { label: string; onClick: () => void }; onDismiss?: () => void; busy?: boolean }) {
  const cls = tone === "error" ? "bg-error-soft text-error" : tone === "warn" ? "bg-amber-soft text-[rgb(var(--c-amber-text))]" : "bg-accent-soft text-ink";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`relative flex min-h-10 shrink-0 items-center gap-3 border-b border-rule px-4 py-1.5 text-[14px] ${cls}`}>
      <Fi g={tone === "info" ? G.info : tone === "warn" ? G.warn : G.error} />
      <div className="min-w-0 flex-1 select-text whitespace-pre-line">{title && <b className="mr-2 font-semibold">{title}</b>}{children}</div>
      {action && <button className="btn-secondary h-7 shrink-0 px-3 text-[13px]" onClick={action.onClick}>{action.label}</button>}
      {onDismiss && <button className="cmd h-7 min-w-7 shrink-0" onClick={onDismiss} aria-label="Dismiss" title="Dismiss"><Fi g={G.cancel} /></button>}
      {busy && <div className="progress-indeterminate absolute inset-x-0 bottom-0 rounded-none" aria-hidden />}
    </div>
  );
}

/** Empty, error or unreachable: what happened, what to do next. */
export function EmptyState({ title, text, action, secondary }: { title: string; text?: ReactNode; action?: { label: string; onClick: () => void }; secondary?: { label: string; onClick: () => void } }) {
  return (
    <div className="mx-auto flex max-w-[560px] flex-col items-start gap-1 px-6 py-12">
      <h2 className="text-[16px] font-semibold">{title}</h2>
      {text && <p className="text-[14px] text-sub">{text}</p>}
      {(action || secondary) && (
        <div className="mt-3 flex gap-2">
          {action && <button className="btn-primary h-8 px-4 text-[14px]" onClick={action.onClick}>{action.label}</button>}
          {secondary && <button className="btn-secondary h-8 px-4 text-[14px]" onClick={secondary.onClick}>{secondary.label}</button>}
        </div>
      )}
    </div>
  );
}

export function SkeletonRows({ n = 8, h = 36 }: { n?: number; h?: number }) {
  return <div aria-hidden className="space-y-1 px-2 pt-2">{Array.from({ length: n }, (_, i) => <div key={i} className="skeleton !rounded" style={{ height: h - 4 }} />)}</div>;
}

export interface MenuItem { id: string; label?: string; shortcut?: string; onClick?: () => void; checked?: boolean; sep?: boolean }

/** A menu at a screen position. Arrow keys move, Enter picks, Esc closes; focus returns to where it was. */
export function Menu({ items, x, y, onClose, label }: { items: MenuItem[]; x: number; y: number; onClose: () => void; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x, top: y });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({ left: Math.max(4, Math.min(x, window.innerWidth - r.width - 4)), top: Math.max(4, Math.min(y, window.innerHeight - r.height - 4)) });
  }, [x, y]);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    const close = () => closeRef.current();
    // A press on the open trigger is left to its own click, which toggles the menu shut (else mousedown closes and click reopens).
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as Element).closest?.('[aria-haspopup="menu"][aria-expanded="true"]')) close(); };
    document.addEventListener("mousedown", down, true);
    window.addEventListener("blur", close);
    return () => { document.removeEventListener("mousedown", down, true); window.removeEventListener("blur", close); opener?.focus?.(); };
  }, []);
  const onKey = (e: KeyboardEvent) => {
    const els = [...(ref.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])];
    const at = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); els[(at + 1) % els.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); els[(at - 1 + els.length) % els.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); els[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); els[els.length - 1]?.focus(); }
    else if (e.key === "Tab") { e.preventDefault(); onClose(); }
  };
  const radio = items.some((i) => i.checked !== undefined);
  return (
    <div ref={ref} role="menu" aria-label={label} className="menu anim-pop fixed z-50 min-w-[200px]" style={pos} onKeyDown={onKey} onContextMenu={(e) => e.preventDefault()}>
      {items.map((it) => it.sep
        ? <div key={it.id} role="separator" className="menu-sep" />
        : (
          <button key={it.id} role={radio ? "menuitemradio" : "menuitem"} aria-checked={radio ? !!it.checked : undefined} className="menu-item" onClick={() => { onClose(); it.onClick?.(); }}>
            {radio && <span className="w-4">{it.checked && <Fi g={G.check} />}</span>}
            <span className="flex-1">{it.label}</span>
            {it.shortcut && <span className="text-[12px] text-sub">{it.shortcut}</span>}
          </button>
        ))}
    </div>
  );
}

/** A command bar button: icon and label (the label hides on narrow windows; the tooltip stays). */
export function CommandButton({ icon, label, onClick, pressed, disabled, busy, title, menu }: { icon?: string; label: string; onClick?: () => void; pressed?: boolean; disabled?: boolean; busy?: boolean; title?: string; menu?: { items: MenuItem[]; label?: string } }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  return (
    <>
      <button
        className="cmd text-[14px]"
        title={title ?? label}
        aria-label={label}
        aria-pressed={pressed}
        aria-haspopup={menu ? "menu" : undefined}
        aria-expanded={menu ? !!at : undefined}
        disabled={disabled}
        onClick={(e) => {
          if (!menu) { onClick?.(); return; }
          const r = e.currentTarget.getBoundingClientRect();
          setAt(at ? null : { x: r.left, y: r.bottom + 4 });
        }}
      >
        {busy ? <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-sub border-t-transparent" aria-hidden /> : icon && <Fi g={icon} />}
        <span className="cmd-label max-[999px]:hidden">{label}</span>
        {menu && <Fi g={G.down} size={12} />}
      </button>
      {menu && at && <Menu items={menu.items} x={at.x} y={at.y} onClose={() => setAt(null)} label={menu.label ?? label} />}
    </>
  );
}

export function CommandBar({ children }: { children: ReactNode }) {
  return <div role="toolbar" aria-label="Commands" className="flex h-11 shrink-0 items-center gap-1 border-b border-rule px-3">{children}</div>;
}
export const CommandDivider = () => <span aria-hidden className="mx-1 h-5 w-px bg-rule-strong" />;

/** "This folder | Everywhere": the scope is always visible. */
export function ScopeSwitch({ value, folderName, onChange }: { value: "folder" | "everywhere"; folderName: string; onChange: (v: "folder" | "everywhere") => void }) {
  return (
    <div role="radiogroup" aria-label="Where to search" className="seg">
      <button role="radio" aria-checked={value === "folder"} title={`Search inside ${folderName}`} onClick={() => onChange("folder")}>This folder</button>
      <button role="radio" aria-checked={value === "everywhere"} title="Search all indexed files" onClick={() => onChange("everywhere")}>Everywhere</button>
    </div>
  );
}

/** A section header with a chevron that collapses it, as "Quick access" in Explorer. */
export function SectionHeader({ title, open, onToggle, extra }: { title: string; open: boolean; onToggle: () => void; extra?: ReactNode }) {
  return (
    <div className="flex items-center gap-1 px-4 pt-3">
      <button className="flex h-8 items-center gap-2 rounded px-1 text-[14px] font-semibold hover:bg-[var(--hover)]" aria-expanded={open} onClick={onToggle}>
        <Fi g={open ? G.down : G.right} size={12} />{title}
      </button>
      {extra}
    </div>
  );
}

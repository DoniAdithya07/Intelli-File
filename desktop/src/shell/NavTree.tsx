import { KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { browseList, BrowseDrive } from "../backend";
import { samePath } from "../lib/format";
import { Location, PAGE_TITLES } from "../lib/nav";
import { toggleFav } from "../lib/favorites";
import { Fi, FolderIcon, G, Menu } from "../ui/controls";

export interface Pinned { name: string; path: string; user: boolean }

interface Node {
  id: string;
  label: string;
  depth: number;
  icon: ReactNode;
  loc: Location | null;
  expandable?: boolean;
  expanded?: boolean;
  dim?: boolean;
  tip?: string;
  pin?: Pinned;
}

interface Props {
  location: Location;
  onNavigate: (l: Location) => void;
  pinned: Pinned[];
  drives: BrowseDrive[] | null;
  rail: boolean;
  footer: ReactNode;
}

const PAGES: { id: Location["kind"]; icon: string }[] = [
  { id: "search", icon: G.search }, { id: "ask", icon: G.chat }, { id: "foryou", icon: G.person }, { id: "activity", icon: G.history }, { id: "index", icon: G.folder }, { id: "settings", icon: G.settings }, { id: "help", icon: G.help },
];

type Kids = { state: "loading" } | { state: "error" } | { state: "ready"; dirs: { name: string; path: string }[] };

export const driveName = (d: BrowseDrive) => `${d.label || (d.type === "removable" ? "Removable Disk" : d.type === "cdrom" ? "DVD Drive" : d.type === "network" ? "Network Drive" : "Local Disk")} (${d.letter}:)`;

/** The left tree: Home, Photos, pinned folders, This PC with its drives, then the IntelliFile pages. One tab stop; arrows move. */
export function NavTree({ location, onNavigate, pinned, drives, rail, footer }: Props) {
  const [open, setOpen] = useState<Set<string>>(new Set(["thispc"]));
  const [kids, setKids] = useState<Record<string, Kids>>({});
  const [focusId, setFocusId] = useState("home");
  const [menu, setMenu] = useState<{ x: number; y: number; pin: Pinned } | null>(null);
  const nav = useRef<HTMLElement>(null);

  // Folders under an expanded drive or folder load when it is opened, and again if it is reopened after an error.
  useEffect(() => {
    open.forEach((id) => {
      if (!id.startsWith("dir:")) return;
      const path = id.slice(4);
      if (kids[path]) return;
      setKids((k) => ({ ...k, [path]: { state: "loading" } }));
      browseList(path, 0, 1000)
        .then((l) => setKids((k) => ({ ...k, [path]: { state: "ready", dirs: l.entries.filter((e) => e.is_dir).map((e) => ({ name: e.name, path: e.path })) } })))
        .catch(() => setKids((k) => ({ ...k, [path]: { state: "error" } })));
    });
  }, [open, kids]);

  const here = (path: string) => location.kind === "folder" && samePath(location.path, path);

  const { top, thisPc, pinnedNodes } = useMemo(() => {
    const top: Node[] = [
      { id: "home", label: "Home", depth: 0, icon: <Fi g={G.home} size={20} />, loc: { kind: "home" } },
      { id: "photos", label: "Photos", depth: 0, icon: <Fi g={G.picture} size={20} />, loc: { kind: "photos" } },
    ];
    const pinnedNodes: Node[] = pinned.map((p) => ({ id: `pin:${p.path}`, label: p.name, depth: 0, icon: <FolderIcon size={20} />, loc: { kind: "folder", path: p.path } as Location, pin: p }));
    const thisPc: Node[] = [{ id: "thispc", label: "This PC", depth: 0, icon: <Fi g={G.thispc} size={20} />, loc: { kind: "thispc" }, expandable: true, expanded: open.has("thispc") }];
    const addDir = (path: string, depth: number, label: string, icon: ReactNode, dim?: boolean) => {
      const id = `dir:${path}`;
      const isOpen = open.has(id);
      thisPc.push({ id, label, depth, icon, loc: dim ? null : { kind: "folder", path }, expandable: !dim, expanded: isOpen, dim, tip: dim ? "Not available" : undefined });
      if (!isOpen) return;
      const k = kids[path];
      if (!k || k.state === "loading") thisPc.push({ id: `${id}:loading`, label: "Loading...", depth: depth + 1, icon: null, loc: null, dim: true });
      else if (k.state === "error") thisPc.push({ id: `${id}:err`, label: "Couldn't open this folder", depth: depth + 1, icon: null, loc: null, dim: true });
      else k.dirs.forEach((d) => addDir(d.path, depth + 1, d.name, <FolderIcon size={20} />));
    };
    if (open.has("thispc")) {
      if (drives === null) thisPc.push({ id: "drives:loading", label: "Loading...", depth: 1, icon: null, loc: null, dim: true });
      drives?.forEach((d) => addDir(d.path, 1, driveName(d), <Fi g={G.drive} size={20} />, !d.ready));
    }
    return { top, thisPc, pinnedNodes };
  }, [pinned, drives, open, kids]);

  const pageNodes: Node[] = PAGES.map((p) => ({ id: `page:${p.id}`, label: PAGE_TITLES[p.id], depth: 0, icon: <Fi g={p.icon} size={20} />, loc: { kind: p.id } as Location }));
  const nodes = [...top, ...pinnedNodes, ...thisPc, ...pageNodes];

  const current = (n: Node): boolean => {
    if (!n.loc) return false;
    if (n.loc.kind === "folder") return here(n.loc.path);
    if (n.loc.kind === "search") return location.kind === "search";
    return n.loc.kind === location.kind;
  };

  const toggle = (id: string, to?: boolean) => setOpen((s) => {
    const n = new Set(s);
    const want = to ?? !n.has(id);
    if (want) n.add(id); else n.delete(id);
    return n;
  });

  const activate = (n: Node) => {
    setFocusId(n.id);
    if (n.loc) onNavigate(n.loc);
  };

  function onKey(e: KeyboardEvent) {
    const els = [...(nav.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])];
    const at = els.indexOf(document.activeElement as HTMLElement);
    const node = nodes.find((n) => n.id === focusId);
    const go = (i: number) => { e.preventDefault(); const el = els[Math.max(0, Math.min(els.length - 1, i))]; el?.focus(); setFocusId(el?.dataset.id ?? focusId); };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(els.length - 1);
    else if (e.key === "ArrowRight" && node?.expandable) { e.preventDefault(); if (!node.expanded) toggle(node.id, true); else go(at + 1); }
    else if (e.key === "ArrowLeft" && node?.expandable && node.expanded) { e.preventDefault(); toggle(node.id, false); }
    else if (e.key === "Enter" && node) { e.preventDefault(); activate(node); }
    else if ((e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) && node?.pin?.user) {
      e.preventDefault();
      const r = (document.activeElement as HTMLElement).getBoundingClientRect();
      setMenu({ x: r.left + 24, y: r.bottom, pin: node.pin });
    }
  }

  const row = (n: Node) => {
    const isCur = current(n);
    return (
      <div
        key={n.id}
        role="treeitem"
        data-id={n.id}
        aria-current={isCur ? "page" : undefined}
        aria-expanded={n.expandable ? !!n.expanded : undefined}
        aria-level={n.depth + 1}
        aria-disabled={n.dim || undefined}
        tabIndex={focusId === n.id ? 0 : -1}
        title={rail || n.tip ? (n.tip ? `${n.label}: ${n.tip}` : n.label) : undefined}
        className={`tree-row flex h-8 cursor-default items-center gap-2 pr-2 text-[14px] ${n.dim ? "text-dim" : ""} ${isCur ? "font-semibold" : ""} ${rail && n.depth > 0 ? "hidden" : ""}`}
        style={{ paddingLeft: rail ? 14 : 8 + 16 * n.depth }}
        onClick={() => activate(n)}
        onFocus={() => setFocusId(n.id)}
        onContextMenu={(e) => { if (n.pin?.user) { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, pin: n.pin }); } }}
      >
        {!rail && (n.expandable ? (
          <button tabIndex={-1} aria-hidden className="grid h-5 w-4 place-items-center text-sub" onClick={(e) => { e.stopPropagation(); toggle(n.id); }}><Fi g={n.expanded ? G.down : G.right} size={10} /></button>
        ) : <span className="w-4 shrink-0" />)}
        <span className="grid w-5 shrink-0 place-items-center" style={n.dim ? { opacity: 0.55 } : undefined}>{n.icon}</span>
        {!rail && <span className="min-w-0 flex-1 truncate">{n.label}</span>}
        {!rail && n.pin && <Fi g={G.pin} size={12} className="text-dim" />}
      </div>
    );
  };

  return (
    <nav ref={nav} aria-label="Folders and pages" className="flex min-h-0 flex-1 flex-col bg-nav">
      <div role="tree" aria-label="Folders and pages" className="flex min-h-0 flex-1 flex-col" onKeyDown={onKey}>
        <div className="min-h-0 flex-1 overflow-y-auto px-1 py-2">
          {top.map(row)}
          {pinnedNodes.length > 0 && <div role="separator" className="mx-2 my-2 h-px bg-rule" />}
          {pinnedNodes.map(row)}
          <div role="separator" className="mx-2 my-2 h-px bg-rule" />
          {thisPc.map(row)}
        </div>
        <div className="shrink-0 border-t border-rule px-1 pb-1 pt-2">
          {!rail && <div className="px-3 pb-1 text-[12px] text-dim">IntelliFile</div>}
          <div role="group" aria-label="IntelliFile">{pageNodes.map(row)}</div>
        </div>
      </div>
      {footer}
      {menu && (
        <Menu x={menu.x} y={menu.y} label="Pinned folder" onClose={() => setMenu(null)}
          items={[{ id: "unpin", label: "Unpin from the left list", onClick: () => toggleFav("pin", { path: menu.pin.path, name: menu.pin.name, isDir: true }) }]} />
      )}
    </nav>
  );
}


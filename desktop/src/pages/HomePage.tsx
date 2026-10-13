import { ReactNode, useEffect, useMemo, useState } from "react";
import { browseQuickAccess, browseRecent, BrowseKind, engineUnreachable, listEvents, StatusResponse, UsageEvent } from "../backend";
import { ActEntry, useActions, Notify } from "../lib/actions";
import { baseName, dateText, naturalCompare, samePath } from "../lib/format";
import { useFavs } from "../lib/favorites";
import { Location } from "../lib/nav";
import { useStatusBar } from "../shell/ctx";
import { CommandBar, CommandButton, EmptyState, Fi, G, Menu, MenuItem, Pills, SectionHeader, SkeletonRows } from "../ui/controls";
import { Column, FileTable, Sort } from "../ui/FileTable";
import { NameCell } from "../ui/NameCell";

interface Row extends ActEntry { key: string; kind: BrowseKind; accessed: number | null; activity: string }
type Pill = "recent" | "favorites" | "frequent";

interface Props {
  status: StatusResponse | null;
  onNavigate: (l: Location) => void;
  notify: Notify;
  onIndexChanged: () => void;
  refreshKey: number;
}

/** Home: where you go often (Quick access), and the files you used lately. */
export function HomePage({ status, onNavigate, notify, onIndexChanged, refreshKey }: Props) {
  const roots = useMemo(() => (status?.folders ?? []).map((f) => f.path), [status?.folders]);
  const actions = useActions(onNavigate, notify, roots, onIndexChanged);
  const [pill, setPill] = useState<Pill>("recent");
  const [quickOpen, setQuickOpen] = useState(true);
  const [tableOpen, setTableOpen] = useState(true);
  const [quick, setQuick] = useState<Row[] | null>(null);
  const [recent, setRecent] = useState<Row[] | null>(null);
  const [events, setEvents] = useState<UsageEvent[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>({ col: "accessed", dir: "desc" });
  const [ctx, setCtx] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const pins = useFavs("pin");
  const favs = useFavs("fav");

  useEffect(() => {
    let dead = false;
    setFailed(null);
    Promise.all([browseQuickAccess(), browseRecent(50), listEvents(500).catch(() => ({ events: [] as UsageEvent[], total: 0 }))])
      .then(([q, r, ev]) => {
        if (dead) return;
        setQuick(q.items.map((i) => ({ key: i.path, path: i.path, name: i.name, is_dir: true, indexed: i.kind === "pinned", kind: "folder", accessed: null, activity: "" })));
        setRecent(r.items.map((i) => ({ key: i.path, path: i.path, name: i.name, is_dir: false, indexed: i.indexed, file_id: i.file_id, kind: i.kind, accessed: i.accessed, activity: i.activity })));
        setEvents(ev.events);
      })
      .catch((e) => { if (!dead) { setQuick([]); setRecent([]); setFailed(engineUnreachable(e) ? "" : "Home could not be loaded. The search engine did not answer."); } });
    return () => { dead = true; };
  }, [refreshKey]);

  // Quick access: the Windows folders, folders pinned here, and the folders that are indexed.
  const quickRows = useMemo<Row[]>(() => {
    const rows = [...(quick ?? [])];
    for (const p of pins) if (!rows.some((r) => samePath(r.path, p.path))) rows.push({ key: p.path, path: p.path, name: p.name, is_dir: true, indexed: false, kind: "folder", accessed: null, activity: "" });
    for (const f of status?.folders ?? []) if (f.exists && f.kind !== "file" && !rows.some((r) => samePath(r.path, f.path))) rows.push({ key: f.path, path: f.path, name: baseName(f.path), is_dir: true, indexed: true, kind: "folder", accessed: null, activity: "" });
    return rows.map((r) => ({ ...r, indexed: r.indexed || roots.some((x) => samePath(x, r.path)) }));
  }, [quick, pins, status?.folders, roots]);

  const frequent = useMemo<Row[]>(() => {
    const cutoff = Date.now() / 1000 - 30 * 86400;
    const counts = new Map<string, { n: number; last: number; id: string | null; path: string }>();
    for (const e of events ?? []) {
      if (e.kind !== "file_opened" || !e.path || e.ts < cutoff) continue;
      const k = e.path.toLowerCase();
      const c = counts.get(k);
      counts.set(k, { n: (c?.n ?? 0) + 1, last: Math.max(c?.last ?? 0, e.ts), id: e.file_id, path: e.path });
    }
    return [...counts.entries()].filter(([, c]) => c.n >= 3).sort((a, b) => b[1].n - a[1].n).slice(0, 50)
      .map(([k, c]) => ({ key: k, path: c.path, name: baseName(c.path), is_dir: false, indexed: true, file_id: c.id, kind: "other" as BrowseKind, accessed: c.last, activity: `Opened ${c.n} times` }));
  }, [events]);

  const favRows = useMemo<Row[]>(() => favs.map((f) => {
    const seen = recent?.find((r) => samePath(r.path, f.path));
    return { key: f.path, path: f.path, name: f.name, is_dir: f.isDir, indexed: seen?.indexed ?? false, file_id: seen?.file_id, kind: (f.isDir ? "folder" : "other") as BrowseKind, accessed: seen?.accessed ?? null, activity: "Favorite" };
  }), [favs, recent]);

  const rows = pill === "recent" ? recent : pill === "favorites" ? favRows : frequent;
  const sorted = useMemo(() => {
    const list = [...(rows ?? [])];
    const dir = sort.dir === "asc" ? 1 : -1;
    list.sort((a, b) => (sort.col === "name" ? dir * naturalCompare(a.name, b.name) : dir * ((a.accessed ?? 0) - (b.accessed ?? 0))));
    return list;
  }, [rows, sort]);

  const toggleSort = (col: string) => setSort((s) => (s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: col === "name" ? "asc" : "desc" }));
  const [selected, setSelected] = useState<Row[]>([]);
  useStatusBar({ left: selected.length ? `${selected.length} item${selected.length === 1 ? "" : "s"} selected` : `${sorted.length} item${sorted.length === 1 ? "" : "s"}` });

  const quickCols: Column<Row>[] = [
    { id: "name", label: "Name", width: "minmax(240px,1fr)", render: (r) => <NameCell name={r.name} isDir size={24} /> },
    { id: "indexed", label: "Searchable", width: "140px", render: (r) => <span className="text-sub">{r.indexed ? "Indexed" : ""}</span> },
  ];
  const tableCols: Column<Row>[] = [
    { id: "name", label: "Name", width: "minmax(240px,1fr)", sortable: true, render: (r) => <NameCell name={r.name} isDir={r.is_dir} size={24} /> },
    { id: "accessed", label: pill === "frequent" ? "Last opened" : "Date accessed", width: "200px", sortable: true, render: (r) => <span className="text-sub">{r.accessed ? dateText(r.accessed) : "Never"}</span> },
    { id: "activity", label: "Activity", width: "minmax(180px,260px)", render: (r) => <span className="block truncate text-sub" title={r.activity}>{r.activity}</span> },
  ];
  const onContext = (list: Row[], x: number, y: number) => setCtx({ x, y, items: actions.menu(list) });

  const historyOff = !!status && !status.activity.enabled;
  let empty: ReactNode = null;
  if (!sorted.length && rows !== null && !failed) {
    if (pill === "recent") empty = historyOff
      ? <EmptyState title="Activity history is off" text="Turn it on to see recent files here." action={{ label: "Open Settings", onClick: () => onNavigate({ kind: "settings" }) }} />
      : <EmptyState title="Nothing opened yet" text="Files you open with IntelliFile will show up here." />;
    else if (pill === "favorites") empty = <EmptyState title="No favorites yet" text="Right-click a file or folder and choose Add to Favorites." />;
    else empty = <EmptyState title="Nothing used often yet" text="Files you open three or more times show up here." />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar>
        <CommandButton icon={G.sort} label="Sort" menu={{ items: [
          { id: "name", label: "Name", checked: sort.col === "name", onClick: () => setSort({ col: "name", dir: "asc" }) },
          { id: "accessed", label: "Date accessed", checked: sort.col === "accessed", onClick: () => setSort({ col: "accessed", dir: "desc" }) },
        ] }} />
        <span className="flex-1" />
        <CommandButton icon={G.more} label="More" menu={{ items: [
          { id: "settings", label: "Settings", onClick: () => onNavigate({ kind: "settings" }) },
          { id: "help", label: "Help", onClick: () => onNavigate({ kind: "help" }) },
        ] }} />
      </CommandBar>
      {failed && <div role="alert" className="px-4 py-3 text-[14px] text-error">{failed}</div>}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <SectionHeader title="Quick access" open={quickOpen} onToggle={() => setQuickOpen((o) => !o)} />
        {quickOpen && (quick === null ? <SkeletonRows n={4} h={40} /> : (
          <div className="flex shrink-0 flex-col" style={{ height: Math.min(Math.max(quickRows.length, 1), 6) * 40 + 32 }}>
            <FileTable label="Quick access" columns={quickCols} items={quickRows.map((r) => ({ key: r.key, data: r }))} rowHeight={40}
              getName={(r) => r.name} onOpen={actions.open} onReveal={actions.reveal} onCopyPaths={actions.copyPaths} onContext={onContext} />
          </div>
        ))}
        <div className="flex items-center gap-3 px-4 pt-3">
          <button className="flex h-8 items-center rounded px-1 hover:bg-[var(--hover)]" aria-expanded={tableOpen} aria-label="Show or hide the file list" onClick={() => setTableOpen((o) => !o)}><Fi g={tableOpen ? G.down : G.right} size={12} /></button>
          <Pills label="Which files" value={pill} onChange={(p) => { setPill(p); if (p === "frequent") setSort({ col: "accessed", dir: "desc" }); }} items={[
            { id: "recent", label: "Recent", icon: G.history }, { id: "favorites", label: "Favorites", icon: G.star }, { id: "frequent", label: "Frequently used", icon: G.sync },
          ]} />
        </div>
        {tableOpen && (rows === null ? <SkeletonRows /> : empty ?? (
          <div className="flex min-h-[200px] flex-1 flex-col">
            <FileTable label={pill === "recent" ? "Recent files" : pill === "favorites" ? "Favorites" : "Frequently used files"} columns={tableCols} items={sorted.map((r) => ({ key: r.key, data: r }))} rowHeight={36}
              sort={sort} onSort={toggleSort} getName={(r) => r.name} onOpen={actions.open} onReveal={actions.reveal} onCopyPaths={actions.copyPaths} onContext={onContext} onSelection={setSelected} />
          </div>
        ))}
      </div>
      {ctx && <Menu x={ctx.x} y={ctx.y} items={ctx.items} label="Item actions" onClose={() => setCtx(null)} />}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowseDrive, browseDrives, engineUnreachable, forgetFolder, indexFolder, reindexFolder, StatusResponse } from "../backend";
import { isInside, samePath, sizeText } from "../lib/format";
import { Location } from "../lib/nav";
import { Notify } from "../lib/actions";
import { useStatusBar } from "../shell/ctx";
import { driveName } from "../shell/NavTree";
import { CommandBar, CommandButton, EmptyState, Fi, G, Menu, MenuItem, SkeletonRows } from "../ui/controls";
import { Column, FileTable, Sort } from "../ui/FileTable";
import { FileGrid } from "../ui/FileGrid";

interface Props {
  status: StatusResponse | null;
  onNavigate: (l: Location) => void;
  notify: Notify;
  onIndexChanged: () => void;
  refreshKey: number;
}

type View = "icons" | "details";

/** This PC: each drive with how full it is and how much of it IntelliFile has indexed. */
export function ThisPcPage({ status, onNavigate, notify, onIndexChanged, refreshKey }: Props) {
  const [drives, setDrives] = useState<BrowseDrive[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [view, setView] = useState<View>("icons");
  const [sort, setSort] = useState<Sort>({ col: "name", dir: "asc" });
  const [selected, setSelected] = useState<BrowseDrive[]>([]);
  const [ctx, setCtx] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [tries, setTries] = useState(0);

  useEffect(() => {
    const on = (e: Event) => setView((e as CustomEvent<View>).detail);
    window.addEventListener("intellifile:view", on);
    return () => window.removeEventListener("intellifile:view", on);
  }, []);

  useEffect(() => {
    let dead = false;
    setFailed(null);
    browseDrives().then((r) => { if (!dead) setDrives(r.drives); })
      .catch((e) => { if (!dead) { setDrives([]); setFailed(engineUnreachable(e) ? "" : "The drives could not be listed. The search engine did not answer."); } });
    return () => { dead = true; };
  }, [refreshKey, tries]);

  const indexedOn = (d: BrowseDrive) => (status ? (status.folders ?? []).filter((f) => isInside(f.path, d.path)).reduce((n, f) => n + f.documents + f.photos + f.videos + f.audio, 0) : null);
  const sorted = useMemo(() => {
    const list = [...(drives ?? [])];
    const dir = sort.dir === "asc" ? 1 : -1;
    list.sort((a, b) => (sort.col === "free" ? dir * ((a.free_bytes ?? 0) - (b.free_bytes ?? 0)) : dir * driveName(a).localeCompare(driveName(b))));
    return list;
  }, [drives, sort]);

  const onView = useCallback((v: "details" | "list" | "icons") => setView(v === "details" ? "details" : "icons"), []);
  useStatusBar({ left: selected.length ? `${selected.length} item${selected.length === 1 ? "" : "s"} selected` : `${sorted.length} item${sorted.length === 1 ? "" : "s"}`, view, onView: onView });

  const open = (d: BrowseDrive) => onNavigate({ kind: "folder", path: d.path });
  const menu = (list: BrowseDrive[], x: number, y: number) => {
    const d = list[0];
    if (!d || list.length !== 1) return;
    const run = async (job: Promise<{ error?: string }>) => { try { const r = await job; if (r.error) notify(r.error); onIndexChanged(); } catch (e) { notify(e instanceof Error ? e.message : String(e)); } };
    const isRoot = (status?.folders ?? []).some((f) => samePath(f.path, d.path));
    const items: MenuItem[] = [{ id: "open", label: "Open", shortcut: "Enter", onClick: () => open(d) }];
    if (d.ready && status?.access.allows_indexing) {
      items.push({ id: "sep", sep: true });
      items.push(isRoot ? { id: "re", label: "Re-index this drive", onClick: () => run(reindexFolder(d.path)) } : { id: "idx", label: "Index this drive", onClick: () => run(indexFolder(d.path)) });
      if (isRoot) items.push({ id: "forget", label: "Remove from index", onClick: () => run(forgetFolder(d.path)) });
    }
    setCtx({ x, y, items });
  };

  const items = sorted.map((d) => ({ key: d.path, data: d }));
  const cols: Column<BrowseDrive>[] = [
    { id: "name", label: "Name", width: "minmax(220px,1fr)", sortable: true, render: (d) => <span className="flex items-center gap-3"><Fi g={G.drive} size={20} />{driveName(d)}</span> },
    { id: "type", label: "Type", width: "140px", render: (d) => <span className="text-sub">{d.type === "fixed" ? "Local Disk" : d.type === "network" ? "Network drive" : d.type === "cdrom" ? "DVD drive" : "Removable"}</span> },
    { id: "total", label: "Total size", width: "120px", align: "right", render: (d) => <span className="text-sub">{sizeText(d.total_bytes)}</span> },
    { id: "free", label: "Free space", width: "120px", align: "right", sortable: true, render: (d) => <span className="text-sub">{sizeText(d.free_bytes)}</span> },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar>
        <CommandButton icon={G.sort} label="Sort" menu={{ items: [
          { id: "name", label: "Name", checked: sort.col === "name", onClick: () => setSort({ col: "name", dir: "asc" }) },
          { id: "free", label: "Free space", checked: sort.col === "free", onClick: () => setSort({ col: "free", dir: "desc" }) },
        ] }} />
        <CommandButton icon={G.view} label="View" menu={{ items: [
          { id: "icons", label: "Large icons", shortcut: "Ctrl+Shift+2", checked: view === "icons", onClick: () => setView("icons") },
          { id: "details", label: "Details", shortcut: "Ctrl+Shift+6", checked: view === "details", onClick: () => setView("details") },
        ] }} />
      </CommandBar>
      {failed && <EmptyState title="The drives could not be listed" text={failed || "The search engine isn't running. Browsing needs it."} action={{ label: "Try again", onClick: () => setTries((t) => t + 1) }} />}
      {drives === null && <SkeletonRows n={3} h={72} />}
      {drives !== null && !failed && view === "icons" && (
        <>
          <h2 className="px-4 pt-3 text-[14px] font-semibold">Devices and drives</h2>
          <FileGrid label="Devices and drives" items={items} columns="repeat(auto-fill, 256px)" gap={16}
            tileLabel={(d) => `${driveName(d)}${d.ready ? `, ${sizeText(d.free_bytes)} free of ${sizeText(d.total_bytes)}` : ", not available"}`}
            getName={(d) => driveName(d)} onOpen={open} onContext={menu} onSelection={setSelected}
            renderTile={(d) => <DriveTile d={d} indexed={indexedOn(d)} />} />
        </>
      )}
      {drives !== null && !failed && view === "details" && (
        <FileTable label="Devices and drives" columns={cols} items={items} rowHeight={40} sort={sort}
          onSort={(c) => setSort((s) => (s.col === c ? { col: c, dir: s.dir === "asc" ? "desc" : "asc" } : { col: c, dir: c === "free" ? "desc" : "asc" }))}
          getName={driveName} onOpen={open} onContext={menu} onSelection={setSelected} />
      )}
      {ctx && <Menu x={ctx.x} y={ctx.y} items={ctx.items} label="Drive actions" onClose={() => setCtx(null)} />}
    </div>
  );
}

function DriveTile({ d, indexed }: { d: BrowseDrive; indexed: number | null }) {
  const used = d.total_bytes && d.free_bytes != null ? Math.round(((d.total_bytes - d.free_bytes) / d.total_bytes) * 100) : null;
  return (
    <div className={`flex min-h-16 items-start gap-3 ${d.ready ? "" : "text-dim"}`} title={d.ready ? undefined : "Not available"}>
      <span className="grid h-10 w-10 shrink-0 place-items-center" style={d.ready ? undefined : { opacity: 0.5 }}><Fi g={G.drive} size={36} /></span>
      <div className="min-w-0 flex-1 text-[14px] leading-5">
        <div className="truncate">{driveName(d)}</div>
        {d.ready && used !== null ? (
          <>
            <div className="drive-bar mt-0.5" role="img" aria-label={`${used}% full`}><i className={used > 90 ? "full" : ""} style={{ width: `${used}%` }} /></div>
            <div className="text-[12px] text-sub">{sizeText(d.free_bytes)} free of {sizeText(d.total_bytes)}</div>
          </>
        ) : <div className="text-[12px]">{d.ready ? "" : "Not available"}</div>}
        {d.ready && indexed !== null && <div className="text-[12px] text-sub">{indexed > 0 ? `${indexed.toLocaleString()} files indexed` : "Not indexed"}</div>}
      </div>
    </div>
  );
}

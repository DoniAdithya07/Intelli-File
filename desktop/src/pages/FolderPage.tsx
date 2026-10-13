import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, browseList, BrowseEntry, forgetFolder, indexFolder, reindexFolder, StatusResponse } from "../backend";
import { useActions, Notify } from "../lib/actions";
import { baseName, dateText, isInside, naturalCompare, samePath, sizeText, typeName } from "../lib/format";
import { Location } from "../lib/nav";
import { useStatusBar } from "../shell/ctx";
import { BrowsePreview } from "../ui/BrowsePreview";
import { Banner, CommandBar, CommandButton, CommandDivider, EmptyState, Fi, FolderIcon, G, Menu, MenuItem, SkeletonRows } from "../ui/controls";
import { Column, FileTable, Sort } from "../ui/FileTable";
import { FileGrid } from "../ui/FileGrid";
import { FileIcon } from "../ui/kit";
import { NameCell } from "../ui/NameCell";
import { Thumb } from "../ui/Thumb";

type View = "details" | "list" | "icons";
type TypeFilter = "" | "document" | "image" | "audio" | "video" | "other";
type WhenFilter = "" | "today" | "week" | "month" | "earlier";
const TYPE_LABEL: Record<Exclude<TypeFilter, "">, string> = { document: "Documents", image: "Pictures", audio: "Audio", video: "Video", other: "Other" };
const WHEN_LABEL: Record<Exclude<WhenFilter, "">, string> = { today: "Today", week: "This week", month: "This month", earlier: "Earlier" };
const DAY = 86400;

interface Props {
  path: string;
  status: StatusResponse | null;
  /** Typed in the search box while its scope is "This folder": shows only matching names. */
  nameFilter: string;
  notify: Notify;
  onNavigate: (l: Location) => void;
  onIndexChanged: () => void;
  refreshKey: number;
  preview: boolean;
  onPreview: (b: boolean) => void;
}

function matchesType(e: BrowseEntry, f: TypeFilter): boolean {
  if (!f) return true;
  if (f === "other") return e.kind === "other" || e.kind === "archive" || e.kind === "code";
  return e.kind === f;
}
function matchesWhen(e: BrowseEntry, f: WhenFilter, now: number): boolean {
  if (!f) return true;
  const age = now - e.modified;
  return f === "today" ? age < DAY : f === "week" ? age < 7 * DAY : f === "month" ? age < 30 * DAY : age >= 30 * DAY;
}

/** One folder, as in Explorer: details, list or large icons, sortable, with a preview pane and a menu of what can be done. */
export function FolderPage({ path, status, nameFilter, notify, onNavigate, onIndexChanged, refreshKey, preview, onPreview }: Props) {
  const [entries, setEntries] = useState<BrowseEntry[] | null>(null);
  const [listing, setListing] = useState<{ name: string; parent: string | null } | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [tries, setTries] = useState(0);
  const [view, setView] = useState<View>("details");
  const [sort, setSort] = useState<Sort>({ col: "name", dir: "asc" });
  const [typeF, setTypeF] = useState<TypeFilter>("");
  const [whenF, setWhenF] = useState<WhenFilter>("");
  const [indexedOnly, setIndexedOnly] = useState(false);
  const [selected, setSelected] = useState<BrowseEntry[]>([]);
  const [ctx, setCtx] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const roots = useMemo(() => (status?.folders ?? []).map((f) => f.path), [status?.folders]);
  const actions = useActions(onNavigate, notify, roots, onIndexChanged);

  // Ctrl+Shift+6 / Ctrl+Shift+2 (sent by App).
  useEffect(() => {
    const on = (e: Event) => setView((e as CustomEvent<View>).detail);
    window.addEventListener("intellifile:view", on);
    return () => window.removeEventListener("intellifile:view", on);
  }, []);

  // Load the folder a page at a time, so rows appear as they arrive.
  useEffect(() => {
    const ctl = new AbortController();
    setEntries(null); setError(null); setSelected([]);
    (async () => {
      let offset = 0, all: BrowseEntry[] = [];
      for (;;) {
        const page = await browseList(path, offset, 1000, ctl.signal);
        if (ctl.signal.aborted) return;
        all = all.concat(page.entries);
        setListing({ name: page.name, parent: page.parent });
        setEntries(all);
        if (!page.has_more) break;
        offset += page.limit;
      }
    })().catch((e) => {
      if (ctl.signal.aborted) return;
      setEntries([]);
      setError(e instanceof ApiError ? { status: e.status, message: e.detail } : { status: 0, message: "" });
    });
    return () => ctl.abort();
  }, [path, refreshKey, tries]);

  const now = Date.now() / 1000;
  const shown = useMemo(() => {
    const q = nameFilter.trim().toLowerCase();
    const list = (entries ?? []).filter((e) => (!q || e.name.toLowerCase().includes(q)) && (e.is_dir ? !indexedOnly || e.indexed : matchesType(e, typeF) && matchesWhen(e, whenF, now) && (!indexedOnly || e.indexed)));
    const dir = sort.dir === "asc" ? 1 : -1;
    const by = (a: BrowseEntry, b: BrowseEntry) => {
      switch (sort.col) {
        case "modified": return a.modified - b.modified;
        case "type": return naturalCompare(typeName(a.name, a.kind), typeName(b.name, b.kind)) || naturalCompare(a.name, b.name);
        case "size": return (a.size ?? 0) - (b.size ?? 0);
        default: return naturalCompare(a.name, b.name);
      }
    };
    return list.sort((a, b) => (a.is_dir !== b.is_dir ? (a.is_dir ? -1 : 1) : dir * by(a, b)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, nameFilter, typeF, whenF, indexedOnly, sort]);

  const isRoot = roots.some((r) => samePath(r, path));
  const covered = roots.some((r) => isInside(path, r));
  const job = status?.job;
  const indexing = !!job && ((job.state === "running" && !!job.folder && (isInside(job.folder, path) || isInside(path, job.folder))) || job.queued.some((q) => samePath(q, path)));
  const name = listing?.name ?? baseName(path);

  const run = async (job: Promise<{ error?: string }>) => {
    try { const r = await job; if (r.error) notify(r.error); onIndexChanged(); } catch (e) { notify(e instanceof Error ? e.message : String(e)); }
  };

  const total = entries?.length ?? 0;
  const selSize = selected.reduce((n, e) => n + (e.size ?? 0), 0);
  const left = selected.length
    ? `${selected.length} item${selected.length === 1 ? "" : "s"} selected${selSize ? `  ${sizeText(selSize)}` : ""}`
    : shown.length !== total ? `${shown.length} of ${total} items` : `${total} item${total === 1 ? "" : "s"}`;
  const progress = indexing && job?.state === "running" ? `   Indexing ${baseName(job.folder ?? path)}, ${job.done} of ${job.total}` : "";
  useStatusBar({ left: error ? "" : left + progress, view, onView: setView });

  const toggleSort = (col: string) => setSort((s) => (s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: col === "modified" ? "desc" : "asc" }));
  // Right-click on a row: that item's menu. On empty space: the folder's own (view, sort, refresh, index).
  const onContext = useCallback((list: BrowseEntry[], x: number, y: number) => {
    if (list.length) { setCtx({ x, y, items: actions.menu(list) }); return; }
    const items: MenuItem[] = [
      { id: "details", label: "Details view", onClick: () => setView("details") },
      { id: "list", label: "List view", onClick: () => setView("list") },
      { id: "icons", label: "Large icons view", onClick: () => setView("icons") },
      { id: "sep1", sep: true },
      { id: "sortn", label: "Sort by name", onClick: () => setSort({ col: "name", dir: "asc" }) },
      { id: "sortd", label: "Sort by date modified", onClick: () => setSort({ col: "modified", dir: "desc" }) },
      { id: "refresh", label: "Refresh", shortcut: "F5", onClick: () => setTries((t) => t + 1) },
    ];
    if (!covered && status?.access.allows_indexing !== false) items.push({ id: "sep2", sep: true }, { id: "index", label: "Index this folder", onClick: () => run(indexFolder(path)) });
    setCtx({ x, y, items });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actions, covered, status?.access.allows_indexing, path]);

  const cols: Column<BrowseEntry>[] = [
    { id: "name", label: "Name", width: "minmax(240px,1fr)", sortable: true, render: (e) => <NameCell name={e.name} isDir={e.is_dir} size={24} /> },
    { id: "modified", label: "Date modified", width: "170px", sortable: true, render: (e) => <span className="text-sub">{dateText(e.modified)}</span> },
    { id: "type", label: "Type", width: "170px", sortable: true, render: (e) => <span className="block truncate text-sub">{typeName(e.name, e.kind)}</span> },
    { id: "size", label: "Size", width: "90px", align: "right", sortable: true, render: (e) => <span className="text-sub">{e.is_dir ? "" : sizeText(e.size)}</span> },
    { id: "indexed", label: "Searchable", width: "100px", render: (e) => <span className="text-sub">{e.indexed ? "Yes" : ""}</span> },
  ];

  const filtersOn = (typeF ? 1 : 0) + (whenF ? 1 : 0) + (indexedOnly ? 1 : 0);
  const clearFilters = () => { setTypeF(""); setWhenF(""); setIndexedOnly(false); };

  const bad = error && (error.status === 403
    ? { title: "You don't have permission to open this folder.", text: error.message || undefined, action: { label: "Show in Explorer", onClick: () => actions.reveal({ path, name, is_dir: true, indexed: false }) } }
    : error.status === 404 && /drive/i.test(error.message)
      ? { title: `${path.slice(0, 2)} isn't available`, text: "It may be unplugged, locked or offline. Files from here that were indexed can still be found by search, but can't be opened until it is back.", action: { label: "Try again", onClick: () => setTries((t) => t + 1) }, secondary: { label: "Go to Home", onClick: () => onNavigate({ kind: "home" }) } }
      : error.status === 404
        ? { title: "This folder was moved or deleted.", action: { label: "Go to Home", onClick: () => onNavigate({ kind: "home" }) } }
        : error.status === 0
          ? { title: "This folder isn't listed yet.", text: "The search engine is not answering yet. The list loads by itself as soon as it is ready.", action: { label: "Try again", onClick: () => setTries((t) => t + 1) }, secondary: { label: "Go to Home", onClick: () => onNavigate({ kind: "home" }) } }
          : { title: "This folder can't be opened.", text: error.message || undefined, action: { label: "Go to Home", onClick: () => onNavigate({ kind: "home" }) } });

  const items = useMemo(() => shown.map((e) => ({ key: e.path, data: e })), [shown]);
  const common = { getName: (e: BrowseEntry) => e.name, onOpen: actions.open, onReveal: actions.reveal, onCopyPaths: actions.copyPaths, onContext, onSelection: setSelected };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar>
        {!error && entries !== null && (indexing
          ? <CommandButton icon={G.newFolder} label="Indexing..." busy disabled />
          : isRoot
            ? <CommandButton icon={G.sync} label="Re-index" onClick={() => run(reindexFolder(path))} menu={{ label: "Index", items: [{ id: "re", label: "Re-index this folder", onClick: () => run(reindexFolder(path)) }, { id: "forget", label: "Remove from index", onClick: () => run(forgetFolder(path)) }] }} />
            : covered ? null
              : status?.access.allows_indexing !== false && <CommandButton icon={G.newFolder} label="Index folder" onClick={() => run(indexFolder(path))} />)}
        <CommandDivider />
        <CommandButton icon={G.sort} label="Sort" menu={{ items: [
          ...(["name", "modified", "type", "size"] as const).map((c) => ({ id: c, label: { name: "Name", modified: "Date modified", type: "Type", size: "Size" }[c], checked: sort.col === c, onClick: () => setSort({ col: c, dir: c === "modified" ? "desc" : "asc" }) })),
          { id: "sep", sep: true },
          { id: "asc", label: "Ascending", checked: sort.dir === "asc", onClick: () => setSort((s) => ({ ...s, dir: "asc" })) },
          { id: "desc", label: "Descending", checked: sort.dir === "desc", onClick: () => setSort((s) => ({ ...s, dir: "desc" })) },
        ] }} />
        <CommandButton icon={G.view} label="View" menu={{ items: [
          { id: "details", label: "Details", shortcut: "Ctrl+Shift+6", checked: view === "details", onClick: () => setView("details") },
          { id: "list", label: "List", checked: view === "list", onClick: () => setView("list") },
          { id: "icons", label: "Large icons", shortcut: "Ctrl+Shift+2", checked: view === "icons", onClick: () => setView("icons") },
        ] }} />
        <CommandButton icon={G.filter} label={filtersOn ? `Filter (${filtersOn})` : "Filter"} menu={{ label: "Filter", items: [
          ...(Object.keys(TYPE_LABEL) as (keyof typeof TYPE_LABEL)[]).map((t) => ({ id: `t-${t}`, label: TYPE_LABEL[t], checked: typeF === t, onClick: () => setTypeF(typeF === t ? "" : t) })),
          { id: "s1", sep: true },
          ...(Object.keys(WHEN_LABEL) as (keyof typeof WHEN_LABEL)[]).map((w) => ({ id: `w-${w}`, label: WHEN_LABEL[w], checked: whenF === w, onClick: () => setWhenF(whenF === w ? "" : w) })),
          { id: "s2", sep: true },
          { id: "idx", label: "Indexed only", checked: indexedOnly, onClick: () => setIndexedOnly(!indexedOnly) },
        ] }} />
        <CommandButton icon={G.more} label="More" menu={{ items: [
          { id: "refresh", label: "Refresh", shortcut: "F5", onClick: () => setTries((t) => t + 1) },
          { id: "copy", label: "Copy path of this folder", onClick: () => actions.copyPaths([{ path, name, is_dir: true, indexed: covered }]) },
          { id: "how", label: "How indexing works", onClick: () => onNavigate({ kind: "help" }) },
        ] }} />
        <span className="flex-1" />
        <CommandButton icon={G.pane} label="Preview" pressed={preview} onClick={() => onPreview(!preview)} title="Preview pane (Alt+P)" />
      </CommandBar>
      {filtersOn > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-rule px-4 py-1 text-[12px]">
          {typeF && <Chip label={TYPE_LABEL[typeF]} onClear={() => setTypeF("")} />}
          {whenF && <Chip label={WHEN_LABEL[whenF]} onClear={() => setWhenF("")} />}
          {indexedOnly && <Chip label="Indexed only" onClear={() => setIndexedOnly(false)} />}
          <button className="text-accent hover:underline" onClick={clearFilters}>Clear all</button>
        </div>
      )}
      {isRoot && indexing && <Banner tone="info" busy>IntelliFile is indexing this folder. You can keep browsing.</Banner>}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {bad && <EmptyState {...bad} />}
          {!bad && entries === null && <SkeletonRows n={8} h={36} />}
          {!bad && entries !== null && entries.length === 0 && <EmptyState title="This folder is empty" text="Files you add will show up here." />}
          {!bad && entries !== null && entries.length > 0 && shown.length === 0 && (
            <EmptyState title={nameFilter.trim() ? `No names in ${name} contain "${nameFilter.trim()}".` : "Nothing here matches the filter."} text={nameFilter.trim() ? "Press Enter to search the contents of the files too." : undefined}
              action={filtersOn ? { label: "Clear filters", onClick: clearFilters } : undefined} />
          )}
          {!bad && shown.length > 0 && view === "details" && <FileTable label={`Files in ${name}`} columns={cols} items={items} rowHeight={36} sort={sort} onSort={toggleSort} {...common} />}
          {!bad && shown.length > 0 && view === "list" && (
            <FileGrid label={`Files in ${name}`} items={items} columns="repeat(auto-fill, minmax(240px, 1fr))" gap={0} tileLabel={(e) => e.name} renderTile={(e) => <div className="flex h-8 items-center"><NameCell name={e.name} isDir={e.is_dir} size={20} /></div>} {...common} />
          )}
          {!bad && shown.length > 0 && view === "icons" && (
            <FileGrid label={`Files in ${name}`} items={items} columns="repeat(auto-fill, 112px)" gap={8} tileLabel={(e) => e.name} {...common}
              renderTile={(e) => (
                <div className="flex w-[104px] flex-col items-center gap-1 text-center">
                  <div className="grid h-[96px] w-[96px] place-items-center overflow-hidden">
                    {e.is_dir ? <FolderIcon size={80} />
                      : (e.kind === "image" || e.kind === "video") && e.indexed ? <Thumb path={e.path} size={192} alt="" className="max-h-[96px] max-w-[96px] rounded object-cover" boxClassName="h-[96px] w-[96px]" />
                        : <FileIcon filename={e.name} size={64} />}
                  </div>
                  <span className="line-clamp-2 w-full break-words text-[12px] leading-4" title={e.name}>{e.name}</span>
                </div>
              )} />
          )}
        </div>
        {preview && (
          <aside aria-label="Preview" className="w-[360px] shrink-0 border-l border-rule bg-content">
            <BrowsePreview selection={selected} onOpen={actions.open} onReveal={actions.reveal} onCopy={actions.copyPaths} />
          </aside>
        )}
      </div>
      {ctx && <Menu x={ctx.x} y={ctx.y} items={ctx.items} label="Item actions" onClose={() => setCtx(null)} />}
    </div>
  );
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex h-6 items-center gap-1 rounded bg-[var(--pressed)] pl-2 pr-1 text-[12px]">
      {label}
      <button className="grid h-5 w-5 place-items-center rounded hover:bg-[var(--hover)]" aria-label={`Remove filter ${label}`} onClick={onClear}><Fi g={G.cancel} size={10} /></button>
    </span>
  );
}


import { BrowseKind } from "../backend";
import { ActEntry } from "../lib/actions";
import { dateText, sizeText, typeName } from "../lib/format";
import { canOpen } from "./ResultCard";
import { FileIcon } from "./kit";
import { FolderIcon } from "./controls";
import { Thumb } from "./Thumb";

export interface PreviewEntry extends ActEntry { kind: BrowseKind; size: number | null; modified: number }

/** The right-hand pane in a folder: what is known about the selected item. Nothing is read from the file here. */
export function BrowsePreview({ selection, onOpen, onReveal, onCopy }: { selection: PreviewEntry[]; onOpen: (e: PreviewEntry) => void; onReveal: (e: PreviewEntry) => void; onCopy: (e: PreviewEntry[]) => void }) {
  if (selection.length === 0) return <div className="grid h-full place-items-center px-6 text-center text-[14px] text-sub">Select a file or folder to see its details.</div>;
  if (selection.length > 1) {
    const total = selection.reduce((n, e) => n + (e.size ?? 0), 0);
    return (
      <div className="px-5 py-4">
        <h2 className="text-[14px] font-semibold">{selection.length} items selected</h2>
        {total > 0 && <p className="mt-1 text-[14px] text-sub">{sizeText(total)} in files</p>}
        <button className="btn-secondary mt-3 h-8 px-3 text-[14px]" onClick={() => onCopy(selection)}>Copy paths</button>
      </div>
    );
  }
  const e = selection[0];
  const folder = e.path.replace(/[\\/][^\\/]*$/, "");
  const picture = (e.kind === "image" || e.kind === "video") && e.indexed;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="grid h-[180px] place-items-center rounded bg-[var(--hover)]">
          {e.is_dir ? <FolderIcon size={96} /> : picture ? <Thumb path={e.path} size={360} alt="" className="max-h-[180px] max-w-full rounded object-contain" boxClassName="h-[180px] w-full" /> : <FileIcon filename={e.name} size={72} />}
        </div>
        <h2 className="mt-4 break-words text-[14px] font-semibold select-text">{e.name}</h2>
        <dl className="mt-2 space-y-1 text-[14px]">
          <Fact k="Type" v={typeName(e.name, e.kind)} />
          {!e.is_dir && <Fact k="Size" v={sizeText(e.size)} />}
          <Fact k="Modified" v={dateText(e.modified)} />
          <Fact k="Searchable" v={e.indexed ? "Indexed" : "Not indexed"} />
          <div><dt className="text-[12px] text-dim">Location</dt><dd className="mono break-all text-[12px] select-text">{folder}</dd></div>
        </dl>
      </div>
      <div className="flex shrink-0 gap-2 border-t border-rule px-5 py-3">
        {(e.is_dir || (e.indexed && canOpen(e.path))) && <button className="btn-primary h-8 px-4 text-[14px]" onClick={() => onOpen(e)}>Open</button>}
        <button className="btn-secondary h-8 px-3 text-[14px]" onClick={() => onReveal(e)}>Show in Explorer</button>
        <button className="btn-secondary h-8 px-3 text-[14px]" onClick={() => onCopy([e])}>Copy path</button>
      </div>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return <div className="flex gap-3"><dt className="w-20 shrink-0 text-[12px] leading-5 text-dim">{k}</dt><dd className="min-w-0 select-text">{v}</dd></div>;
}

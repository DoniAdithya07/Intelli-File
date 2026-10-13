import { ReactNode } from "react";
import { FolderIcon } from "./controls";
import { FileIcon } from "./kit";

/** Icon and name, as in Explorer's Name column. */
export function NameCell({ name, isDir, size = 24, dim, children }: { name: string; isDir: boolean; size?: number; dim?: boolean; children?: ReactNode }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      {isDir ? <FolderIcon size={size} dim={dim} /> : <FileIcon filename={name} size={size} />}
      <span className="min-w-0 flex-1 truncate" title={name}>{name}{children}</span>
    </span>
  );
}

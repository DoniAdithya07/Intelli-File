import { FILE_MANAGER, MOD_KEY } from "../platform";

const SYNTAX: [string, string][] = [
  ["type:pdf", "only one kind of file (also type:document, type:image, type:audio)"],
  ["after:2026-03-01", "modified on or after a date (after:2026 and after:2026-03 work too)"],
  ["before:2026-04-01", "modified before a date"],
  ["size:>10mb", "larger than a size (size:<500kb for smaller)"],
  ["in:invoices", "the folder path contains this word"],
  ['"exact words"', "the whole phrase, in this order"],
  ["? your question", "ask a question and get an answer from your files"],
];

const KEYS: [string, string][] = [
  ["Up / Down", "move through the results"],
  ["Enter", "open the selected file"],
  [`${MOD_KEY}+Enter`, `show the selected file in ${FILE_MANAGER}`],
  ["Esc", "clear the search"],
  ["Ctrl+Space", "quick search from anywhere in Windows"],
];

/** Help: the search words and keys. Stage D adds the full guide (docs/UI_DESIGN.md section 13). */
export function HelpPage() {
  const table = (rows: [string, string][]) => (
    <table className="mt-2 w-full max-w-[720px] overflow-hidden rounded-lg border border-rule bg-content text-[13px]">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-rule last:border-b-0">
            <td className="mono w-[200px] px-4 py-2 align-top text-ink">{k}</td>
            <td className="px-4 py-2 text-ink/80">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div className="h-full overflow-y-auto">
      <h1 className="text-[26px] font-semibold leading-tight">Help</h1>
      <h2 className="mt-5 text-[15px] font-semibold">Search words</h2>
      <p className="mt-1 text-[13px] text-ink/70">Add these to a search, or pick them under Filters.</p>
      {table(SYNTAX)}
      <h2 className="mt-6 text-[15px] font-semibold">Keys</h2>
      {table(KEYS)}
    </div>
  );
}

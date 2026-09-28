import { Fragment, ReactNode, useMemo, useRef, useState } from "react";
// The manual is one file, docs/USER_MANUAL.md, bundled at build time: the
// Help page and the document on GitHub can never say different things.
import manual from "../../../docs/USER_MANUAL.md?raw";

/** Inline Markdown the manual uses: `code`, **bold** and *italic*. */
function inline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i} className="mono rounded bg-ink/[0.07] px-1 text-[12.5px]">{p.slice(1, -1)}</code>;
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i} className="font-semibold">{p.slice(2, -2)}</strong>;
    if (p.startsWith("*") && p.endsWith("*") && p.length > 2) return <em key={i}>{p.slice(1, -1)}</em>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}

interface Section { id: string; title: string; lines: string[] }

/** Splits the manual at its "## " headings; the text before the first one is the introduction. */
function parse(md: string): { title: string; intro: string[]; sections: Section[] } {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let title = "Help";
  const intro: string[] = [];
  const sections: Section[] = [];
  for (const line of lines) {
    if (line.startsWith("# ")) title = line.slice(2).trim();
    else if (line.startsWith("## ")) {
      const t = line.slice(3).trim();
      sections.push({ id: t.toLowerCase().replace(/[^a-z0-9]+/g, "-"), title: t, lines: [] });
    } else if (sections.length) sections[sections.length - 1].lines.push(line);
    else intro.push(line);
  }
  return { title, intro, sections };
}

/** Block Markdown: paragraphs, "- " and "1. " lists (with indented follow-on text), and tables. */
function Blocks({ lines }: { lines: string[] }) {
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].split("|").slice(1, -1).map((c) => c.trim());
        if (!cells.every((c) => /^-+$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      out.push(
        <table key={out.length} className="my-3 w-full overflow-hidden rounded-lg border border-rule bg-content text-[13px]">
          <thead><tr className="border-b border-rule text-left">{head.map((c, j) => <th key={j} className="px-3 py-2 font-semibold">{inline(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, k) => <tr key={k} className="border-b border-rule last:border-b-0">{r.map((c, j) => <td key={j} className="px-3 py-2 align-top text-ink/85">{inline(c)}</td>)}</tr>)}</tbody>
        </table>,
      );
      continue;
    }
    const bullet = /^(\s*)- /.exec(line);
    const numbered = /^(\s*)\d+\. /.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      const items: string[][] = [];
      while (i < lines.length) {
        const l = lines[i];
        const start = ordered ? /^\d+\. (.*)$/.exec(l) : /^- (.*)$/.exec(l);
        if (start) { items.push([start[1]]); i++; continue; }
        const nested = /^\s+- (.*)$/.exec(l);
        if (nested && items.length) { items[items.length - 1].push("• " + nested[1]); i++; continue; }
        if (/^\s{2,}\S/.test(l) && items.length) { items[items.length - 1].push(l.trim()); i++; continue; }
        if (!l.trim() && i + 1 < lines.length && (/^\s{2,}\S/.test(lines[i + 1]) || (ordered ? /^\d+\. /.test(lines[i + 1]) : /^- /.test(lines[i + 1])))) { i++; continue; }
        break;
      }
      const List = ordered ? "ol" : "ul";
      out.push(
        <List key={out.length} className={`my-2 space-y-1.5 pl-5 text-[14px] leading-relaxed ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((parts, k) => (
            <li key={k}>{parts.map((p, j) => <span key={j} className={j ? "mt-1 block" : ""}>{inline(p)}</span>)}</li>
          ))}
        </List>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("|") && !/^\s*(- |\d+\. )/.test(lines[i])) { para.push(lines[i].trim()); i++; }
    out.push(<p key={out.length} className="my-2 max-w-[72ch] text-[14px] leading-relaxed text-ink/90">{inline(para.join(" "))}</p>);
  }
  return <>{out}</>;
}

/** Help: the user manual, with its sections listed on the left. */
export function HelpPage() {
  const doc = useMemo(() => parse(manual), []);
  const [current, setCurrent] = useState(doc.sections[0]?.id);
  const scroller = useRef<HTMLDivElement>(null);
  const go = (id: string) => {
    setCurrent(id);
    scroller.current?.querySelector(`#${id}`)?.scrollIntoView({ block: "start" });
  };
  return (
    <div className="flex h-full gap-6">
      <nav aria-label="Manual sections" className="w-[190px] shrink-0 overflow-y-auto">
        <h1 className="text-[26px] font-semibold leading-tight">Help</h1>
        <ul className="mt-3 space-y-0.5 text-[13px]">
          {doc.sections.map((s) => (
            <li key={s.id}>
              <button
                className={`w-full rounded px-2 py-1.5 text-left ${current === s.id ? "bg-ink/[0.07] font-medium text-ink" : "text-ink/75 hover:bg-ink/[0.05] hover:text-ink"}`}
                aria-current={current === s.id ? "true" : undefined}
                onClick={() => go(s.id)}
              >
                {s.title}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <div ref={scroller} className="min-w-0 flex-1 overflow-y-auto pb-10 pr-2 select-text">
        <h2 className="text-[20px] font-semibold">{doc.title}</h2>
        <Blocks lines={doc.intro} />
        {doc.sections.map((s) => (
          <section key={s.id} id={s.id} className="scroll-mt-2 border-t border-rule pt-4 mt-6 first-of-type:mt-4">
            <h2 className="text-[17px] font-semibold">{s.title}</h2>
            <Blocks lines={s.lines} />
          </section>
        ))}
      </div>
    </div>
  );
}

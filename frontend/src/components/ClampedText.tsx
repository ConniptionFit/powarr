import { Fragment, useState } from "react";

// Long note text (Match Notes, LLM Notes, deletion rationales) wraps to the
// available width and clamps at this many characters with a Show more toggle.
export const NOTE_CHAR_LIMIT = 220;

// Minimal, dependency-free renderer for the subset of Markdown the LLM is asked
// for (reply_format: "markdown") — **bold** and "- "/"* " bullet lists. Safe to
// run on plain prose too (no matches = renders unchanged), since llm_rationale
// doesn't record which reply_format produced it and may predate this feature.
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : <Fragment key={i}>{part}</Fragment>
  );
}

function renderMarkdown(text: string): React.ReactNode {
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  let listItems: string[] = [];
  const flushList = (key: string) => {
    if (listItems.length) {
      nodes.push(
        <ul key={key} className="list-disc list-inside my-0.5 space-y-0.5">
          {listItems.map((li, i) => <li key={i}>{renderInline(li)}</li>)}
        </ul>
      );
      listItems = [];
    }
  };
  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      listItems.push(trimmed.slice(2));
    } else {
      flushList(`ul-${i}`);
      nodes.push(<Fragment key={i}>{renderInline(line)}{i < lines.length - 1 ? <br /> : null}</Fragment>);
    }
  });
  flushList("ul-end");
  return nodes;
}

export default function ClampedText({ text, limit = NOTE_CHAR_LIMIT, markdown = false }: {
  text: string;
  limit?: number;
  markdown?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const needsClamp = text.length > limit;
  const shown = open || !needsClamp ? text : text.slice(0, limit).trimEnd() + "…";
  return (
    <span className="block whitespace-normal break-words text-slate-400 text-xs leading-relaxed">
      {markdown ? renderMarkdown(shown) : shown}
      {needsClamp && (
        <button
          onClick={e => { e.stopPropagation(); setOpen(o => !o); }}
          className="ml-1.5 text-brand-light hover:text-white text-[11px] underline underline-offset-2"
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </span>
  );
}

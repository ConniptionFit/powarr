import { useState } from "react";

// Long note text (Match Notes, LLM Notes, deletion rationales) wraps to the
// available width and clamps at this many characters with a Show more toggle.
export const NOTE_CHAR_LIMIT = 220;

export default function ClampedText({ text, limit = NOTE_CHAR_LIMIT }: { text: string; limit?: number }) {
  const [open, setOpen] = useState(false);
  const needsClamp = text.length > limit;
  const shown = open || !needsClamp ? text : text.slice(0, limit).trimEnd() + "…";
  return (
    <span className="block whitespace-normal break-words text-slate-400 text-xs leading-relaxed">
      {shown}
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

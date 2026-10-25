import { useState, useRef } from "react";
import { Bot, Lightbulb } from "lucide-react";
import BotState from "../../../components/BotState";
import { MATCH_TYPE_STYLES } from "./FileDetails";

export function PackReviewButton({
  itemId,
  isLoading,
  hasResults,
  results,
  onClick,
}: {
  itemId: number;
  isLoading: boolean;
  hasResults: boolean;
  results?: Array<{ file: string; season: number; episode: number; match_type: string; confidence: string; reason: string }>;
  onClick: () => void;
}) {
  const [showTooltip, setShowTooltip] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={onClick}
        disabled={isLoading}
        onMouseEnter={() => { if (hasResults) setShowTooltip(true); }}
        onMouseLeave={() => setShowTooltip(false)}
        className="p-1.5 rounded hover:bg-cyan-900/40 text-slate-400 hover:text-cyan-300 transition-colors disabled:opacity-30"
        title={isLoading ? "Analyzing..." : hasResults ? "Hover to see results" : "Review files in pack with LLM"}
      >
        {isLoading ? (
          <BotState variant="thinking" size={15} />
        ) : hasResults ? (
          <Lightbulb size={15} className="text-cyan-300" />
        ) : (
          <Bot size={15} />
        )}
      </button>

      {showTooltip && hasResults && results && (
        <div className="absolute bottom-full right-0 mb-2 bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl p-3 w-64 z-50">
          <div className="max-h-60 overflow-y-auto space-y-2">
            {results.map((m, i) => (
              <div key={i} className="bg-surface/50 border border-purple-900/20 rounded p-2">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-bold text-brand-light">
                    S{m.season.toString().padStart(2, "0")}E{m.episode.toString().padStart(2, "0")}
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${MATCH_TYPE_STYLES[m.match_type] ?? "bg-surface-overlay text-slate-400"}`}>
                    {m.match_type}
                  </span>
                </div>
                <div className="text-xs text-slate-300 truncate" title={m.file}>{m.file}</div>
                {m.reason && <div className="text-xs text-slate-400 mt-1">{m.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


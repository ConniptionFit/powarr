import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Pause, Play, Search, Copy, Download, Check } from "lucide-react";
import { systemApi } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";

const LEVELS = ["ERROR", "WARNING", "INFO", "DEBUG"] as const;
type Level = (typeof LEVELS)[number];

const LEVEL_COLORS: Record<Level, string> = {
  ERROR: "text-red-400",
  WARNING: "text-yellow-400",
  INFO: "text-slate-300",
  DEBUG: "text-slate-500",
};

function levelOf(line: string): Level | null {
  return LEVELS.find(l => line.includes(` ${l} `)) ?? null;
}

export default function LogsPage() {
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [hidden, setHidden] = usePersistedState<Level[]>("logs.hiddenLevels", []);
  const [follow, setFollow] = useState(true);
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data, refetch, isFetching } = useQuery({
    queryKey: ["logs"],
    queryFn: () => systemApi.logs(500),
    refetchInterval: autoRefresh ? 5000 : false,
  });

  const lines = data?.lines ?? [];
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return lines.filter(l => {
      const lvl = levelOf(l);
      if (lvl && hidden.includes(lvl)) return false;
      return !q || l.toLowerCase().includes(q);
    });
  }, [lines, search, hidden]);

  const counts = useMemo(() => {
    const c: Record<Level, number> = { ERROR: 0, WARNING: 0, INFO: 0, DEBUG: 0 };
    for (const l of lines) { const lvl = levelOf(l); if (lvl) c[lvl]++; }
    return c;
  }, [lines]);

  // Tail behaviour: stick to the newest line until the user scrolls up.
  useEffect(() => {
    const el = scrollRef.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [visible, follow]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
  };

  const toggleLevel = (l: Level) =>
    setHidden(h => (h.includes(l) ? h.filter(x => x !== l) : [...h, l]));

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(visible.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable (non-secure context) */ }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([visible.join("\n")], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `powarr-logs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const btn = "flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors";

  return (
    <div className="p-4 sm:p-8 flex flex-col h-full">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Logs</h1>
          <p className="text-slate-400 text-sm mt-1">Recent application log output (in-memory, last 1000 lines)</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setAutoRefresh(a => !a)} className={btn}>
            {autoRefresh ? <Pause size={14} /> : <Play size={14} />}
            {autoRefresh ? "Pause" : "Resume"}
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/20 text-brand-light hover:bg-brand/30 text-sm transition-colors"
          >
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={copy} className={btn} aria-label="Copy visible lines">
            {copied ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy"}
          </button>
          <button onClick={download} className={btn} aria-label="Download visible lines">
            <Download size={14} /> Save
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter lines…"
            aria-label="Filter log lines"
            className="w-full pl-9 pr-3 py-2 rounded-lg bg-surface-overlay border border-purple-900/30 text-sm text-white placeholder-slate-500 outline-none focus:border-brand-light"
          />
        </div>
        {LEVELS.map(l => {
          const off = hidden.includes(l);
          return (
            <button
              key={l}
              onClick={() => toggleLevel(l)}
              aria-pressed={!off}
              className={`px-2.5 py-1.5 rounded-full border text-xs font-medium transition-colors ${
                off
                  ? "border-slate-700 text-slate-600 line-through"
                  : `border-purple-900/50 bg-surface-overlay ${LEVEL_COLORS[l]}`
              }`}
            >
              {l} <span className="opacity-60">{counts[l]}</span>
            </button>
          );
        })}
        <span className="text-xs text-slate-500 ml-auto">
          {visible.length} / {lines.length} lines
        </span>
      </div>

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 bg-surface-raised rounded-xl border border-purple-900/30 p-4 overflow-y-auto font-mono text-xs leading-relaxed relative"
      >
        {visible.length === 0 ? (
          <p className="text-slate-500">
            {lines.length === 0 ? "No log output captured yet." : "No lines match the current filters."}
          </p>
        ) : (
          visible.map((line, i) => (
            <div key={i} className={`whitespace-pre-wrap break-all ${LEVEL_COLORS[levelOf(line) ?? "DEBUG"] ?? "text-slate-400"}`}>{line}</div>
          ))
        )}
        {!follow && (
          <button
            onClick={() => setFollow(true)}
            className="sticky bottom-0 left-full ml-auto block px-3 py-1.5 rounded-full bg-brand text-white text-xs shadow-lg"
          >
            Jump to latest
          </button>
        )}
      </div>
    </div>
  );
}

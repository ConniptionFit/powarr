import { useQuery } from "@tanstack/react-query";
import { fmtBytes, mediaApi, type StorageForecast } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";

const TB = 1024 ** 4;

const STATUS_COPY: Record<StorageForecast["status"], (f: StorageForecast) => string> = {
  no_capacity: () => "Enter your storage capacity to see when the library will fill.",
  growing: f => `At this pace the library fills in about ${f.days_until_full} days (${f.full_date}).`,
  shrinking: () => "The library is shrinking, so it won't fill at this rate.",
  flat: () => "Additions and deletions are balanced, so usage is flat.",
  distant: () => "More than 10 years of headroom at the current pace.",
  full: () => "The library is already at or over the capacity you entered.",
};

// Net growth = bytes added (by Plex added date) minus bytes deleted through
// Powarr. Capacity is typed in (in TB) because Powarr can't see the real disk;
// it's remembered per browser.
export default function StorageForecastCard() {
  const [capTb, setCapTb] = usePersistedState<string>("powarr.storageCapacityTb", "");
  const cap = Number(capTb) > 0 ? Math.round(Number(capTb) * TB) : undefined;
  const { data } = useQuery({
    queryKey: ["storage-forecast", 90, cap ?? 0],
    queryFn: () => mediaApi.storageForecast(90, cap),
  });
  if (!data) return null;

  const net = data.net_bytes_per_day;
  const peak = Math.max(...data.points.map(p => Math.max(p.added, p.removed)), 1);
  const tone = data.status === "full" ? "text-red-400"
    : data.status === "growing" && (data.days_until_full ?? 999) < 90 ? "text-amber-400"
    : "text-slate-300";

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 mb-6">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-3">
        <div>
          <p className="text-slate-400 text-xs uppercase tracking-wider">Storage forecast — last 90 days</p>
          <p className="text-white text-lg font-semibold mt-0.5">
            {fmtBytes(data.current_bytes)}{" "}
            <span className="text-slate-500 text-sm font-normal">in the library</span>
          </p>
          <p className="text-slate-400 text-xs mt-0.5">
            +{fmtBytes(data.added_bytes)} added, −{fmtBytes(data.removed_bytes)} deleted ·{" "}
            <span className="text-white">{net >= 0 ? "+" : "−"}{fmtBytes(Math.abs(net))}/day</span> net
          </p>
        </div>
        <label className="text-xs text-slate-400 flex items-center gap-2">
          Capacity
          <input
            type="number" min="0" step="0.5" inputMode="decimal"
            value={capTb} onChange={e => setCapTb(e.target.value)}
            placeholder="e.g. 24"
            className="w-24 bg-surface border border-purple-900/40 rounded-md px-2 py-1 text-white text-sm focus:outline-none focus:border-brand-light"
            aria-label="Total storage capacity in terabytes"
          />
          TB
        </label>
      </div>

      {data.used_pct != null && (
        <div className="mb-3">
          <div className="h-2 rounded-full bg-surface overflow-hidden" role="progressbar"
               aria-valuenow={Math.min(data.used_pct, 100)} aria-valuemin={0} aria-valuemax={100}
               aria-label="Library size as a share of the capacity you entered">
            <div className="h-full bg-brand-light" style={{ width: `${Math.min(data.used_pct, 100)}%` }} />
          </div>
          <p className="text-slate-500 text-[11px] mt-1">{data.used_pct}% of {fmtBytes(data.capacity_bytes ?? 0)}</p>
        </div>
      )}

      <p className={`text-sm ${tone}`}>{STATUS_COPY[data.status](data)}</p>

      <div className="flex items-end gap-px h-12 mt-3" role="img"
           aria-label="Daily bytes added versus deleted over the last 90 days">
        {data.points.map(p => (
          <div key={p.date} className="flex-1 min-w-0 flex flex-col justify-end h-full gap-px"
               title={`${p.date}: +${fmtBytes(p.added)} / −${fmtBytes(p.removed)}`}>
            <div className="bg-emerald-400/60 rounded-sm" style={{ height: `${(p.added / peak) * 50}%` }} />
            <div className="bg-brand-light/70 rounded-sm" style={{ height: `${(p.removed / peak) * 50}%` }} />
          </div>
        ))}
      </div>
      <p className="text-[10px] text-slate-600 mt-1">Green: added · Purple: deleted through Powarr</p>
    </div>
  );
}

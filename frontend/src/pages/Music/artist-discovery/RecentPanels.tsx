import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, History, UserPlus } from "lucide-react";
import { fmtRelative, parseApiDate } from "../../../lib/api";
import ArtistCard from "../../../components/ArtistCard";
import ScrollFadeX from "../../../components/ScrollFadeX";
import { type DiscoveryRun, type RecentAdd, api, whySuggested } from "./shared";

export function runDuration(r: DiscoveryRun): string {
  if (!r.started_at || !r.finished_at) return "—";
  const secs = Math.max(0, Math.round((parseApiDate(r.finished_at).getTime() - parseApiDate(r.started_at).getTime()) / 1000));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

export function addReason(a: RecentAdd): string {
  if (a.source === "related") return "Added from a Related Artists search";
  if (a.discovery_source) {
    return `Accepted from the discovery queue — ${whySuggested({
      source: a.discovery_source,
      similarity_score: a.similarity_score,
      seed_artist_name: a.seed_artist_name,
      seed_artist_names: a.seed_artist_names,
      associated_seed_mbids: a.associated_seed_mbids,
    })}`;
  }
  return "Accepted from the discovery queue";
}

export function RecentlyAdded() {
  const [open, setOpen] = useState(true);
  const [limit, setLimit] = useState(10);
  const { data: adds = [] } = useQuery({
    queryKey: ["ad-recent-adds", limit],
    queryFn: () => api.recentAdds(limit),
    enabled: open,
  });

  return (
    <section className="mt-8">
      <div className="flex items-center gap-3 mb-3">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider"
        >
          <UserPlus size={16} /> Recently added
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {open && (
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            title="How many recent adds to show"
            className="bg-surface-raised border border-purple-900/40 rounded-lg px-2 py-1 text-xs text-slate-300"
          >
            <option value={10}>Last 10</option>
            <option value={25}>Last 25</option>
            <option value={50}>Last 50</option>
          </select>
        )}
      </div>
      {open &&
        (adds.length === 0 ? (
          <p className="text-slate-500 text-sm">
            No artists added yet — accept a discovery candidate or add one from Related Artists.
          </p>
        ) : (
          <div className="grid gap-2">
            {adds.map((a) => (
              <ArtistCard
                key={a.id}
                name={a.artist_name}
                yearsActive={a.years_active}
                imageUrl={a.image_url}
                bio={a.bio}
                genres={a.genres}
                subtitle={addReason(a)}
                actions={
                  <span
                    className="text-xs text-slate-500 whitespace-nowrap"
                    title={a.added_at ? parseApiDate(a.added_at).toLocaleString() : undefined}
                  >
                    {fmtRelative(a.added_at)}
                  </span>
                }
              />
            ))}
          </div>
        ))}
    </section>
  );
}

export function RecentRuns() {
  const [open, setOpen] = useState(false);
  const { data: runs = [] } = useQuery({ queryKey: ["ad-runs"], queryFn: api.runs, enabled: open });

  return (
    <section className="mt-8">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider mb-3"
      >
        <History size={16} /> Recent runs
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open &&
        (runs.length === 0 ? (
          <p className="text-slate-500 text-sm">No runs recorded yet.</p>
        ) : (
          <ScrollFadeX className="overflow-x-auto" fadeFrom="from-[#1a1a2e]">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-purple-900/30">
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 font-medium">Started</th>
                  <th className="py-2 pr-4 font-medium">Duration</th>
                  <th className="py-2 pr-4 font-medium">Found</th>
                  <th className="py-2 pr-4 font-medium">Added</th>
                  <th className="py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-b border-purple-900/20 text-slate-300">
                    <td className="py-2 pr-4 capitalize">{r.run_type}</td>
                    <td className="py-2 pr-4 whitespace-nowrap">{fmtRelative(r.started_at)}</td>
                    <td className="py-2 pr-4">{runDuration(r)}</td>
                    <td className="py-2 pr-4">{r.candidates_found}</td>
                    <td className="py-2 pr-4">{r.candidates_added}</td>
                    <td className="py-2 text-slate-400">{r.message || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollFadeX>
        ))}
    </section>
  );
}

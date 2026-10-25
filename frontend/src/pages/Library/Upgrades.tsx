import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { integrationsApi, fmtBytes, type UpgradeSection } from "../../lib/api";
import { SkeletonTable } from "../../components/Skeleton";
import ScrollFadeX from "../../components/ScrollFadeX";

function Section({ title, unit, sec }: { title: string; unit: string; sec: UpgradeSection }) {
  return (
    <section className="mb-8">
      <div className="flex items-baseline gap-3 mb-2">
        <h2 className="text-white font-semibold">{title}</h2>
        {sec.configured && !sec.error && (
          <span className="text-slate-500 text-xs">
            {sec.total.toLocaleString()} {unit} below cutoff
            {unit === "episodes"
              ? ` across ${sec.items.length.toLocaleString()} series`
              : sec.total > sec.items.length && ` · showing the largest ${sec.items.length.toLocaleString()}`}
          </span>
        )}
      </div>
      {!sec.configured ? (
        <p className="text-slate-500 text-sm">Not connected. Enable it under Settings, Integrations.</p>
      ) : sec.error ? (
        <p className="text-amber-400 text-sm">Couldn't reach it: {sec.error}</p>
      ) : sec.items.length === 0 ? (
        <p className="text-green-400 text-sm">Everything meets its quality cutoff.</p>
      ) : (
        <ScrollFadeX className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-4 py-2.5 text-left">Title</th>
                <th className="px-4 py-2.5 text-left">Current quality</th>
                <th className="px-4 py-2.5 text-right">{unit === "episodes" ? "Episodes" : ""}</th>
                <th className="px-4 py-2.5 text-right">Size</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/20">
              {sec.items.map(i => (
                <tr key={`${i.id}-${i.title}`} className="hover:bg-white/5">
                  <td className="px-4 py-2 text-white">
                    {i.title}{i.year ? <span className="text-slate-500"> ({i.year})</span> : null}
                    {!i.monitored && <span className="ml-2 text-[10px] text-slate-500 uppercase">unmonitored</span>}
                  </td>
                  <td className="px-4 py-2 text-slate-300">{i.quality ?? "Unknown"}</td>
                  <td className="px-4 py-2 text-right text-slate-400">{i.episodes ?? ""}</td>
                  <td className="px-4 py-2 text-right text-slate-400">{fmtBytes(i.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollFadeX>
      )}
    </section>
  );
}

// "Cutoff unmet" as reported by Radarr and Sonarr themselves. Read-only: upgrading is
// done by the *arr apps (search from their Wanted tab); this just shows where to look.
export default function Upgrades() {
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["upgrade-candidates"],
    queryFn: integrationsApi.upgradeCandidates,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return (
    <div className="p-4 sm:p-8">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-5">
        <p className="text-slate-400 text-sm max-w-2xl">
          Titles whose current file is below the quality profile's cutoff, as reported by Radarr and Sonarr.
          Search for upgrades from their Wanted tab.
        </p>
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm disabled:opacity-50">
          <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
      </div>
      {isLoading || !data ? (
        <SkeletonTable rows={6} cols={4} />
      ) : (
        <>
          <Section title="Movies (Radarr)" unit="movies" sec={data.radarr} />
          <Section title="Series (Sonarr)" unit="episodes" sec={data.sonarr} />
        </>
      )}
    </div>
  );
}

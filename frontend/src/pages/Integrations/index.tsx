import { useQuery } from "@tanstack/react-query";
import { integrationsApi } from "../../lib/api";
import { Skeleton } from "../../components/Skeleton";
import { IntegrationCard } from "./integration-cards/IntegrationCard";
import { OllamaCard } from "./integration-cards/OllamaCard";
import { LastFmCard } from "./integration-cards/LastFmCard";
import { YoutubeCard } from "./integration-cards/YoutubeCard";
import { SpotifyCard } from "./integration-cards/SpotifyCard";
import { QdrantCard } from "./integration-cards/QdrantCard";

export default function IntegrationsPage({ embedded = false }: { embedded?: boolean }) {
  const { data: integrations = [], isLoading } = useQuery({
    queryKey: ["integrations"],
    queryFn: integrationsApi.list,
  });

  const order = ["plex", "jellyfin", "tautulli", "radarr", "sonarr", "lidarr", "readarr", "seerr", "bazarr", "qbittorrent", "transmission"];
  const dedicatedCards = ["lastfm", "youtube", "spotify"];
  const sorted = [...integrations]
    .filter(cfg => !dedicatedCards.includes(cfg.name))
    .sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
  const lastfm = integrations.find(cfg => cfg.name === "lastfm");
  const youtube = integrations.find(cfg => cfg.name === "youtube");
  const spotify = integrations.find(cfg => cfg.name === "spotify");

  const body = isLoading ? (
    <Skeleton className="h-20 w-full" count={4} />
  ) : (
    <div className="space-y-4">
      {sorted.map(cfg => <IntegrationCard key={cfg.name} cfg={cfg} />)}
      <QdrantCard />
      {lastfm && <LastFmCard cfg={lastfm} />}
      {youtube && <YoutubeCard cfg={youtube} />}
      {spotify && <SpotifyCard cfg={spotify} />}
      <OllamaCard />
    </div>
  );

  if (embedded) return <div className="max-w-2xl">{body}</div>;

  return (
    <div className="p-4 sm:p-8 max-w-2xl">
      <h1 className="text-2xl font-bold text-white mb-1">Integrations</h1>
      <p className="text-slate-400 text-sm mb-8">Connect Powarr to your media stack</p>
      {body}
    </div>
  );
}

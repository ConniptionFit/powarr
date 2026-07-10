import { Compass } from "lucide-react";

export default function ArtistDiscovery() {
  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="text-center max-w-md">
        <div className="inline-flex p-4 rounded-full bg-purple-900/30 mb-4">
          <Compass size={32} className="text-brand-light" />
        </div>
        <h1 className="text-xl font-bold text-white mb-2">Artist Discovery</h1>
        <p className="text-slate-400 text-sm mb-4">
          Surfaces new artists from your Qdrant affinity space based on what you already listen
          to — separate from playlist generation.
        </p>
        <span className="inline-block px-3 py-1 rounded-full bg-amber-900/40 text-amber-300 text-xs font-bold uppercase tracking-wide">
          Planned
        </span>
      </div>
    </div>
  );
}

import { useEffect } from "react";
import { X } from "lucide-react";
import { ArtistDiscoverySettingsCard } from "../../Settings/MusicSettings";

/** Slide-over for Music setup: Basics first, Advanced collapsed. Same form as Settings > Music. */
export default function MusicSetupPanel({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Music setup">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <aside className="relative w-full max-w-2xl h-full overflow-y-auto bg-surface-raised border-l border-purple-900/40 p-5 sm:p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold text-white">Music setup</h2>
          <button onClick={onClose} aria-label="Close setup" className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"><X size={18} /></button>
        </div>
        <ArtistDiscoverySettingsCard />
      </aside>
    </div>
  );
}

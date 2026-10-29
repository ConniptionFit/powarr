import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { req } from "../../../lib/api";
import type { ADSettings } from "../../Settings/MusicSettings";
import { ModeSegments, modeOf, patchForMode, type AutoAddMode } from "../../Settings/AutoAddMode";

/** The Review tab's one-click Auto-add mode. Saves straight away. */
export function AutoAddModeBar({ enabled, onOpenSetup }: { enabled: boolean; onOpenSetup: () => void }) {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: () => req<ADSettings>("/artist-discovery/settings") });
  const save = useMutation({
    mutationFn: (mode: AutoAddMode) =>
      req<ADSettings>("/artist-discovery/settings", { method: "PUT", body: JSON.stringify({ ...settings, ...patchForMode(mode, settings ?? {}) }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ad-settings"] }); qc.invalidateQueries({ queryKey: ["ad-status"] }); },
  });
  if (!settings) return null;
  return (
    <div className="flex flex-wrap items-center gap-3 mb-5">
      <span className="text-sm text-white font-semibold">Auto-add</span>
      <ModeSegments value={modeOf(settings)} disabled={save.isPending || !enabled} onChange={(m) => save.mutate(m)} />
      <button onClick={onOpenSetup} className="text-xs text-brand-light hover:underline">Edit rules</button>
      {save.isError && <span className="text-xs text-red-300">{(save.error as Error).message}</span>}
    </div>
  );
}

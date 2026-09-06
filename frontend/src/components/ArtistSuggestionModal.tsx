import { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  X,
  Sparkles,
  Play,
  Pause,
  Compass,
  Zap,
  Network,
  Radio,
  Music2,
  Check,
  AlertTriangle,
  Flame,
  Volume2,
  Layers,
  ArrowUpRight,
  Disc3,
} from "lucide-react";
import { req } from "../lib/api";

export interface SeedInsight {
  name: string;
  musicbrainz_id?: string | null;
  plays_in_library: number;
  in_lidarr: boolean;
  shared_genres: string[];
}

export interface GateInsight {
  lane: string;
  lane_label: string;
  lane_description: string;
  similarity_score?: number | null;
  similarity_percent?: number | null;
  connection_count: number;
  suggest_threshold: number;
  auto_add_threshold: number;
  auto_add_eligible: boolean;
  auto_add_reason: string;
}

export interface TrackPreview {
  id?: number | null;
  title: string;
  duration: number;
  preview?: string | null;
  album?: string | null;
  album_cover?: string | null;
}

export interface CandidateInsights {
  id: number;
  artist_name: string;
  musicbrainz_id?: string | null;
  image_url?: string | null;
  bio?: string | null;
  years_active?: string | null;
  genres: string[];
  shared_genres: string[];
  unique_genres: string[];
  mood_tags: string[];
  era?: string | null;
  gate: GateInsight;
  seeds: SeedInsight[];
  top_tracks: TrackPreview[];
  is_low_metadata: boolean;
  warning?: string | null;
  summary: string;
}

function laneIcon(lane: string) {
  if (lane === "centroid") return <Compass size={14} className="text-purple-400" />;
  if (lane === "centroid_recent") return <Zap size={14} className="text-amber-400" />;
  if (lane.startsWith("centroid_mood_")) return <Flame size={14} className="text-rose-400" />;
  if (lane === "graph") return <Network size={14} className="text-cyan-400" />;
  if (lane === "ingest") return <Radio size={14} className="text-emerald-400" />;
  return <Sparkles size={14} className="text-brand-light" />;
}

function formatDuration(secs: number) {
  if (!secs) return "0:30";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

export default function ArtistSuggestionModal({
  candidateId,
  artistName,
  onClose,
  onAccept,
  onReject,
  actionPending,
}: {
  candidateId: number;
  artistName: string;
  onClose: () => void;
  onAccept?: () => void;
  onReject?: () => void;
  actionPending?: boolean;
}) {
  const { data, isLoading, error } = useQuery<CandidateInsights>({
    queryKey: ["candidate-insights", candidateId],
    queryFn: () => req<CandidateInsights>(`/artist-discovery/candidates/${candidateId}/insights`),
  });

  const [playingTrackId, setPlayingTrackId] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Stop audio on unmount or track change
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const togglePlay = (track: TrackPreview) => {
    if (!track.preview) return;

    if (playingTrackId === track.id) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPlayingTrackId(null);
      return;
    }

    if (audioRef.current) {
      audioRef.current.pause();
    }

    const audio = new Audio(track.preview);
    audio.volume = 0.8;
    audio.play().catch(() => {
      setPlayingTrackId(null);
    });
    audio.onended = () => {
      setPlayingTrackId(null);
      audioRef.current = null;
    };
    audioRef.current = audio;
    setPlayingTrackId(track.id ?? null);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 sm:p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl bg-[#141424] border border-purple-900/40 rounded-2xl shadow-2xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-start justify-between gap-3 px-6 py-5 border-b border-purple-900/30 bg-surface-raised/40 shrink-0">
          <div className="flex items-center gap-4 min-w-0">
            {data?.image_url ? (
              <img
                src={data.image_url}
                alt={artistName}
                className="w-14 h-14 rounded-xl object-cover shrink-0 border border-purple-900/40 shadow-md"
              />
            ) : (
              <div className="w-14 h-14 rounded-xl shrink-0 bg-surface border border-purple-900/40 flex items-center justify-center text-slate-500">
                <Music2 size={24} />
              </div>
            )}
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-purple-400 font-semibold">
                  <Sparkles size={13} /> Suggestion Insights
                </span>
                {data?.years_active && (
                  <span className="text-xs text-slate-500 font-normal">
                    · {data.years_active}
                  </span>
                )}
              </div>
              <h2 className="text-xl font-bold text-white truncate mt-0.5">{artistName}</h2>
              {data?.summary && (
                <p className="text-xs text-slate-400 truncate mt-0.5">{data.summary}</p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1 text-sm">
          {isLoading && (
            <div className="space-y-4 py-8 text-center text-slate-400">
              <div className="inline-block animate-spin text-purple-400 mb-2">
                <Disc3 size={28} />
              </div>
              <p>Analyzing recommendation signals & fetching discography…</p>
            </div>
          )}

          {error && (
            <div className="p-4 rounded-xl bg-red-900/20 border border-red-800/40 text-red-200">
              Couldn't load insights: {error instanceof Error ? error.message : String(error)}
            </div>
          )}

          {data && (
            <>
              {/* Warning banner for low metadata / creator noise */}
              {data.is_low_metadata && (
                <div className="p-3.5 rounded-xl bg-amber-900/25 border border-amber-700/40 text-amber-200 flex items-start gap-3 text-xs leading-relaxed">
                  <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <span className="font-semibold text-amber-300">Metadata Notice:</span>{" "}
                    {data.warning || "Missing MusicBrainz ID and genre tags. This may be a non-music or video creator scrobble."}
                  </div>
                </div>
              )}

              {/* Lane & Match Engine Card */}
              <div className="p-4 rounded-xl bg-surface-raised/70 border border-purple-900/30 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="p-1.5 rounded-lg bg-purple-950/60 border border-purple-800/40">
                      {laneIcon(data.gate.lane)}
                    </span>
                    <div>
                      <p className="text-xs text-slate-400 font-medium">Discovery Lane</p>
                      <p className="text-sm font-semibold text-white">{data.gate.lane_label}</p>
                    </div>
                  </div>

                  {data.gate.similarity_percent != null ? (
                    <div className="flex items-center gap-2 bg-emerald-950/40 border border-emerald-800/50 px-3 py-1.5 rounded-lg">
                      <span className="text-xs text-emerald-400 font-medium">Affinity Score:</span>
                      <span className="text-base font-bold text-emerald-300">
                        {data.gate.similarity_percent}%
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 bg-cyan-950/40 border border-cyan-800/50 px-3 py-1.5 rounded-lg">
                      <span className="text-xs text-cyan-400 font-medium">Connections:</span>
                      <span className="text-base font-bold text-cyan-300">
                        {data.gate.connection_count}
                      </span>
                    </div>
                  )}
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">
                  {data.gate.lane_description}
                </p>

                {/* Automation Gate Proximity */}
                <div className="pt-2 border-t border-purple-900/20 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                  <span className="flex items-center gap-1.5">
                    <Layers size={13} className="text-purple-400" />
                    Auto-Add Status:
                  </span>
                  <span className="text-slate-300">{data.gate.auto_add_reason}</span>
                </div>
              </div>

              {/* Seed Network: Influenced By */}
              {data.seeds.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2.5 flex items-center gap-1.5">
                    <Network size={14} className="text-purple-400" /> Influenced by Library Seeds ({data.seeds.length})
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    {data.seeds.map((s) => (
                      <div
                        key={s.name}
                        className="p-3 rounded-xl bg-surface-raised/40 border border-purple-900/25 flex flex-col justify-between"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="font-semibold text-white text-xs truncate">
                            {s.name}
                          </span>
                          {s.in_lidarr && (
                            <span className="text-[10px] bg-teal-950/60 text-teal-300 border border-teal-800/40 px-1.5 py-0.5 rounded">
                              Lidarr
                            </span>
                          )}
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
                          <span>
                            {s.plays_in_library > 0 ? (
                              <span className="text-purple-300 font-medium">
                                {s.plays_in_library} plays in Plex
                              </span>
                            ) : (
                              <span>In library</span>
                            )}
                          </span>
                          {s.shared_genres.length > 0 && (
                            <span className="text-slate-500 truncate max-w-[120px]" title={s.shared_genres.join(", ")}>
                              {s.shared_genres[0]}
                              {s.shared_genres.length > 1 && ` +${s.shared_genres.length - 1}`}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Musical DNA & Genre Overlap */}
              {(data.shared_genres.length > 0 || data.unique_genres.length > 0) && (
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2.5 flex items-center gap-1.5">
                    <Music2 size={14} className="text-purple-400" /> Musical DNA & Sound
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {data.shared_genres.map((g) => (
                      <span
                        key={g}
                        className="text-xs bg-purple-950/70 border border-purple-700/60 text-purple-200 px-2.5 py-1 rounded-lg flex items-center gap-1 shadow-sm"
                        title="Shared with your library seeds"
                      >
                        <span className="text-brand-light">★</span> {g}
                      </span>
                    ))}
                    {data.unique_genres.map((g) => (
                      <span
                        key={g}
                        className="text-xs bg-surface text-slate-400 border border-purple-900/30 px-2.5 py-1 rounded-lg"
                      >
                        {g}
                      </span>
                    ))}
                    {data.era && (
                      <span className="text-xs bg-surface-raised text-amber-300/90 border border-amber-800/30 px-2.5 py-1 rounded-lg">
                        {data.era}
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Bio snippet */}
              {data.bio && (
                <div className="text-xs text-slate-400 leading-relaxed p-3.5 rounded-xl bg-surface-raised/30 border border-purple-900/20">
                  <p>{data.bio}</p>
                </div>
              )}

              {/* Top Tracks & Audio Previews (Deezer Zero-Config) */}
              <div>
                <div className="flex items-center justify-between mb-2.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                    <Volume2 size={14} className="text-purple-400" /> Top Tracks & 30s Audio Previews
                  </h3>
                  <span className="text-[11px] text-slate-500">Public preview stream</span>
                </div>

                {data.top_tracks.length === 0 ? (
                  <p className="text-xs text-slate-500 italic p-3 rounded-lg bg-surface/30">
                    No preview tracks available for this artist.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {data.top_tracks.map((t) => {
                      const isPlaying = playingTrackId === t.id;
                      return (
                        <div
                          key={t.id ?? t.title}
                          className={`p-2.5 rounded-xl border flex items-center justify-between gap-3 transition-colors ${
                            isPlaying
                              ? "bg-purple-950/40 border-purple-600/60 text-white"
                              : "bg-surface-raised/40 border-purple-900/25 hover:bg-surface-raised/70 text-slate-300"
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            {t.preview ? (
                              <button
                                onClick={() => togglePlay(t)}
                                aria-label={isPlaying ? "Pause preview" : "Play preview"}
                                className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-transform active:scale-95 ${
                                  isPlaying
                                    ? "bg-brand text-white shadow-lg shadow-purple-900/50"
                                    : "bg-purple-900/40 text-purple-300 hover:bg-purple-800/60"
                                }`}
                              >
                                {isPlaying ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
                              </button>
                            ) : (
                              <div className="w-8 h-8 rounded-lg bg-surface/50 text-slate-600 flex items-center justify-center shrink-0">
                                <Music2 size={14} />
                              </div>
                            )}
                            <div className="min-w-0">
                              <p className="text-xs font-medium truncate">{t.title}</p>
                              {t.album && (
                                <p className="text-[11px] text-slate-500 truncate">{t.album}</p>
                              )}
                            </div>
                          </div>

                          <span className="text-xs text-slate-500 font-mono shrink-0">
                            {formatDuration(t.duration)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Modal Actions Footer */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-purple-900/30 bg-surface-raised/50 shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
          >
            Close
          </button>

          <div className="flex items-center gap-2">
            {onReject && (
              <button
                onClick={() => {
                  onReject();
                  onClose();
                }}
                disabled={actionPending}
                className="px-3.5 py-2 rounded-xl text-xs font-medium text-red-300 bg-red-950/40 border border-red-800/40 hover:bg-red-900/50 disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                <X size={14} /> Reject
              </button>
            )}
            {onAccept && (
              <button
                onClick={() => {
                  onAccept();
                  onClose();
                }}
                disabled={actionPending}
                className="px-4 py-2 rounded-xl text-xs font-medium text-white bg-brand hover:bg-brand-light disabled:opacity-50 transition-colors flex items-center gap-1.5 shadow-lg shadow-purple-950/50"
              >
                <Check size={14} /> Accept & Add to Lidarr
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

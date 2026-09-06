import { useState, useRef, useEffect, useCallback } from "react";
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
  Volume1,
  VolumeX,
  Layers,
  Disc3,
  Square,
  Loader2,
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

function formatTime(secs: number) {
  if (isNaN(secs) || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
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

  const [activeTrack, setActiveTrack] = useState<TrackPreview | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isBuffering, setIsBuffering] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(30);
  const [volume, setVolume] = useState(0.8);
  const [isMuted, setIsMuted] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current.onplay = null;
      audioRef.current.onpause = null;
      audioRef.current.onwaiting = null;
      audioRef.current.onplaying = null;
      audioRef.current.ontimeupdate = null;
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current = null;
    }
    setIsPlaying(false);
    setIsBuffering(false);
    setCurrentTime(0);
  }, []);

  const handleClose = useCallback(() => {
    stopAudio();
    onClose();
  }, [stopAudio, onClose]);

  // Stop audio on unmount
  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, [stopAudio]);

  const togglePlay = useCallback((track: TrackPreview) => {
    if (!track.preview) return;

    // If same track is already loaded
    if (activeTrack && (activeTrack.id === track.id || activeTrack.preview === track.preview)) {
      if (audioRef.current) {
        if (isPlaying) {
          audioRef.current.pause();
        } else {
          setIsBuffering(true);
          audioRef.current.play().catch(() => {
            setIsPlaying(false);
            setIsBuffering(false);
          });
        }
      }
      return;
    }

    // New track selected
    stopAudio();
    setActiveTrack(track);
    setIsBuffering(true);
    setCurrentTime(0);
    setDuration(30);

    const audio = new Audio(track.preview);
    audio.volume = isMuted ? 0 : volume;

    audio.onplay = () => {
      setIsPlaying(true);
      setIsBuffering(false);
    };
    audio.onpause = () => {
      setIsPlaying(false);
    };
    audio.onwaiting = () => {
      setIsBuffering(true);
    };
    audio.onplaying = () => {
      setIsBuffering(false);
      setIsPlaying(true);
    };
    audio.ontimeupdate = () => {
      setCurrentTime(audio.currentTime);
      if (audio.duration && !isNaN(audio.duration) && isFinite(audio.duration)) {
        setDuration(audio.duration);
      }
    };
    audio.onended = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    audio.onerror = () => {
      setIsPlaying(false);
      setIsBuffering(false);
    };

    audioRef.current = audio;
    audio.play().catch(() => {
      setIsPlaying(false);
      setIsBuffering(false);
    });
  }, [activeTrack, isPlaying, isMuted, volume, stopAudio]);

  const handleSeek = (time: number) => {
    setCurrentTime(time);
    if (audioRef.current) {
      audioRef.current.currentTime = time;
    }
  };

  const handleVolumeChange = (newVol: number) => {
    setVolume(newVol);
    setIsMuted(false);
    if (audioRef.current) {
      audioRef.current.volume = newVol;
    }
  };

  const toggleMute = () => {
    const next = !isMuted;
    setIsMuted(next);
    if (audioRef.current) {
      audioRef.current.volume = next ? 0 : volume;
    }
  };

  // Keyboard shortcuts: Space toggles play/pause, Escape closes modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && activeTrack) {
        const target = e.target as HTMLElement;
        if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
        e.preventDefault();
        togglePlay(activeTrack);
      } else if (e.code === "Escape") {
        handleClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeTrack, togglePlay, handleClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 sm:p-4 backdrop-blur-sm"
      onClick={handleClose}
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
            onClick={handleClose}
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
                  <div className="space-y-2">
                    {data.top_tracks.map((t, idx) => {
                      const isThisTrack = activeTrack && (activeTrack.id === t.id || activeTrack.preview === t.preview);
                      const isThisPlaying = isThisTrack && isPlaying;
                      const isThisBuffering = isThisTrack && isBuffering;
                      const progressPercent = isThisTrack && duration > 0 ? Math.min(100, Math.max(0, (currentTime / duration) * 100)) : 0;

                      return (
                        <div
                          key={t.id ?? `${t.title}-${idx}`}
                          onClick={() => t.preview && togglePlay(t)}
                          className={`p-3 rounded-xl border flex flex-col gap-2 transition-all cursor-pointer relative overflow-hidden ${
                            isThisPlaying
                              ? "bg-purple-950/60 border-brand shadow-lg shadow-purple-950/50 text-white ring-1 ring-brand/50"
                              : isThisTrack
                              ? "bg-purple-950/30 border-purple-700/50 text-slate-200"
                              : "bg-surface-raised/40 border-purple-900/25 hover:bg-surface-raised/70 text-slate-300 hover:border-purple-800/40"
                          }`}
                        >
                          {/* Background progress fill on active track */}
                          {isThisTrack && (
                            <div
                              className="absolute bottom-0 left-0 top-0 bg-brand/10 pointer-events-none transition-all duration-150"
                              style={{ width: `${progressPercent}%` }}
                            />
                          )}

                          <div className="flex items-center justify-between gap-3 relative z-10">
                            <div className="flex items-center gap-3 min-w-0">
                              {t.preview ? (
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    togglePlay(t);
                                  }}
                                  title={isThisPlaying ? "Pause preview (Space)" : "Play 30s preview (Space)"}
                                  aria-label={isThisPlaying ? "Pause preview" : "Play preview"}
                                  className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all active:scale-95 ${
                                    isThisPlaying
                                      ? "bg-brand text-white shadow-md shadow-brand/40"
                                      : isThisTrack
                                      ? "bg-purple-800/80 text-purple-200"
                                      : "bg-purple-900/40 text-purple-300 hover:bg-purple-800/60 hover:text-white"
                                  }`}
                                >
                                  {isThisBuffering ? (
                                    <Loader2 size={16} className="animate-spin text-brand-light" />
                                  ) : isThisPlaying ? (
                                    <Pause size={16} />
                                  ) : (
                                    <Play size={16} className="ml-0.5" />
                                  )}
                                </button>
                              ) : (
                                <div className="w-9 h-9 rounded-xl bg-surface/50 text-slate-600 flex items-center justify-center shrink-0">
                                  <Music2 size={16} />
                                </div>
                              )}

                              {t.album_cover && (
                                <img
                                  src={t.album_cover}
                                  alt={t.title}
                                  className={`w-9 h-9 rounded-lg object-cover shrink-0 border border-purple-900/30 shadow-sm ${
                                    isThisPlaying ? "ring-2 ring-brand-light ring-offset-1 ring-offset-[#141424]" : ""
                                  }`}
                                />
                              )}

                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <p className="text-xs font-semibold truncate text-white">{t.title}</p>
                                  {isThisPlaying && (
                                    <span className="flex items-center gap-1.5 text-[10px] uppercase font-bold text-emerald-400 bg-emerald-950/70 border border-emerald-800/50 px-2 py-0.5 rounded-full shrink-0">
                                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                                      Playing
                                    </span>
                                  )}
                                  {isThisTrack && !isThisPlaying && !isThisBuffering && (
                                    <span className="text-[10px] uppercase font-bold text-amber-400 bg-amber-950/70 border border-amber-800/50 px-2 py-0.5 rounded-full shrink-0">
                                      Paused
                                    </span>
                                  )}
                                  {isThisBuffering && (
                                    <span className="text-[10px] uppercase font-bold text-purple-300 bg-purple-950/70 border border-purple-800/50 px-2 py-0.5 rounded-full shrink-0">
                                      Buffering…
                                    </span>
                                  )}
                                </div>
                                {t.album && (
                                  <p className="text-[11px] text-slate-400 truncate mt-0.5">{t.album}</p>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center gap-3 shrink-0 relative z-10">
                              {/* Animated Equalizer Wave Bars when playing */}
                              {isThisPlaying && (
                                <div className="flex items-end gap-0.5 h-4 w-5 px-1 py-0.5 bg-purple-950/80 rounded border border-purple-800/40 shrink-0" title="Audio playing">
                                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-1" />
                                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-2" />
                                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-3" />
                                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-4" />
                                </div>
                              )}

                              {/* Time display: live counter if active, or 30s badge */}
                              {isThisTrack ? (
                                <span className="text-xs font-mono font-semibold text-brand-light shrink-0">
                                  {formatTime(currentTime)} <span className="text-slate-500 font-normal">/ 0:30</span>
                                </span>
                              ) : (
                                <span className="text-[11px] font-mono text-slate-400 bg-surface/60 border border-purple-900/30 px-2 py-0.5 rounded shrink-0">
                                  30s preview
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Embedded progress bar when active track */}
                          {isThisTrack && (
                            <div className="w-full bg-purple-950/80 h-1.5 rounded-full overflow-hidden relative z-10">
                              <div
                                className="bg-gradient-to-r from-brand to-brand-light h-full rounded-full transition-all duration-150"
                                style={{ width: `${progressPercent}%` }}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Docked "Now Playing" Audio Player Bar */}
        {activeTrack && (
          <div className="mx-6 mb-3 p-3 rounded-2xl bg-gradient-to-r from-[#1c1236] via-[#151228] to-[#1c1236] border border-brand/50 shadow-2xl shadow-purple-950/80 flex flex-col gap-2 shrink-0 animate-tray-card-enter">
            <div className="flex items-center justify-between gap-3">
              {/* Left: Thumbnail & track info */}
              <div className="flex items-center gap-3 min-w-0">
                <div className="relative shrink-0">
                  {activeTrack.album_cover ? (
                    <img
                      src={activeTrack.album_cover}
                      alt={activeTrack.title}
                      className={`w-10 h-10 rounded-lg object-cover border border-purple-700/50 shadow ${
                        isPlaying ? "ring-2 ring-brand-light" : ""
                      }`}
                    />
                  ) : (
                    <div className="w-10 h-10 rounded-lg bg-purple-900/40 border border-purple-800/40 flex items-center justify-center text-purple-300">
                      <Disc3 size={20} className={isPlaying ? "animate-spin" : ""} />
                    </div>
                  )}
                  {isPlaying && (
                    <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
                    </span>
                  )}
                </div>

                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold tracking-wider uppercase text-purple-400 bg-purple-950/80 border border-purple-800/50 px-1.5 py-0.5 rounded">
                      30s Preview
                    </span>
                    <span className="text-xs text-slate-400 truncate">{artistName}</span>
                  </div>
                  <p className="text-sm font-bold text-white truncate mt-0.5">{activeTrack.title}</p>
                </div>
              </div>

              {/* Center: Equalizer wave */}
              {isPlaying && (
                <div className="hidden sm:flex items-end gap-1 h-5 px-2 py-1 bg-purple-950/80 rounded-lg border border-purple-800/40" title="Preview playing">
                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-1" />
                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-2" />
                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-3" />
                  <span className="w-0.5 bg-brand-light rounded-full animate-sound-bar-4" />
                </div>
              )}

              {/* Right: Master transport + Volume + Dismiss */}
              <div className="flex items-center gap-2 sm:gap-3 shrink-0">
                <button
                  onClick={() => togglePlay(activeTrack)}
                  title={isPlaying ? "Pause (Space)" : "Play (Space)"}
                  aria-label={isPlaying ? "Pause track" : "Play track"}
                  className="w-10 h-10 rounded-full bg-brand hover:bg-brand-light text-white flex items-center justify-center shadow-lg shadow-purple-900/60 transition-all hover:scale-105 active:scale-95"
                >
                  {isBuffering ? (
                    <Loader2 size={18} className="animate-spin text-white" />
                  ) : isPlaying ? (
                    <Pause size={18} />
                  ) : (
                    <Play size={18} className="ml-0.5" />
                  )}
                </button>

                <button
                  onClick={stopAudio}
                  title="Stop preview"
                  aria-label="Stop preview"
                  className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
                >
                  <Square size={15} />
                </button>

                <div className="hidden sm:flex items-center gap-1.5 pl-2 border-l border-purple-800/40">
                  <button
                    onClick={toggleMute}
                    title={isMuted ? "Unmute" : "Mute"}
                    aria-label={isMuted ? "Unmute" : "Mute"}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
                  >
                    {isMuted || volume === 0 ? (
                      <VolumeX size={16} className="text-red-400" />
                    ) : volume < 0.5 ? (
                      <Volume1 size={16} />
                    ) : (
                      <Volume2 size={16} />
                    )}
                  </button>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={isMuted ? 0 : volume}
                    onChange={(e) => handleVolumeChange(Number(e.target.value))}
                    className="w-16 accent-brand-light h-1 bg-purple-950 rounded cursor-pointer"
                    title={`Volume: ${Math.round((isMuted ? 0 : volume) * 100)}%`}
                  />
                </div>
              </div>
            </div>

            {/* Scrubber slider & Time counter */}
            <div className="flex items-center gap-2.5 pt-1">
              <span className="text-[11px] font-mono text-brand-light font-semibold w-8 text-right shrink-0">
                {formatTime(currentTime)}
              </span>
              <input
                type="range"
                min="0"
                max={duration || 30}
                step="0.1"
                value={currentTime}
                onChange={(e) => handleSeek(Number(e.target.value))}
                className="flex-1 accent-brand-light h-1.5 bg-purple-950/80 rounded-full cursor-pointer hover:h-2 transition-all"
                title="Seek preview position"
              />
              <span className="text-[11px] font-mono text-slate-400 w-8 shrink-0">
                {formatTime(duration || 30)}
              </span>
            </div>
          </div>
        )}

        {/* Modal Actions Footer */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-purple-900/30 bg-surface-raised/50 shrink-0">
          <button
            onClick={handleClose}
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

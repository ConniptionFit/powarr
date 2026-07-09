/**
 * BotState — animated LLM/agent status icon.
 *
 * A faithful React + TypeScript port of `BotState.dc.html`. Ten expressive
 * states, each composed from Lucide icons and animated on a gentle loop.
 * Color is semantic — it signals the state at a glance.
 *
 * All artwork is inline SVG (no external assets). Keyframes are injected once
 * into the document head the first time any instance mounts.
 *
 *   <BotState variant="thinking" size={84} />
 *
 * Built from Lucide (ISC/MIT-licensed).
 */
import React, { useEffect } from "react";

export type BotStateVariant =
  | "available"
  | "thinking"
  | "reading"
  | "searching"
  | "responding"
  | "slow"
  | "idea"
  | "complete"
  | "error"
  | "idle";

export interface BotStateProps {
  /** Which status to display. */
  variant: BotStateVariant;
  /** Square size in px (number) or any CSS length (string). Default `84`. */
  size?: number | string;
  /** Animation timing function (maps to `--bs-ease`). Default `ease-in-out`. */
  ease?: string;
  /** Bot line weight (maps to `--bs-stroke`). Default `1.8`. */
  stroke?: number;
  className?: string;
  style?: React.CSSProperties;
}

/** Semantic color per state. */
export const BOT_STATE_COLORS: Record<BotStateVariant, string> = {
  available: "#34d399",
  thinking: "#a78bfa",
  reading: "#38bdf8",
  searching: "#22d3ee",
  responding: "#818cf8",
  slow: "#fbbf24",
  idea: "#facc15",
  complete: "#4ade80",
  error: "#f87171",
  idle: "#94a3b8",
};

export const BOT_STATE_VARIANTS = Object.keys(
  BOT_STATE_COLORS
) as BotStateVariant[];

/** Dark disc behind each corner badge. */
const BADGE_BG = "#12161f";

const KEYFRAMES = `
@keyframes bs-breathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.05)} }
@keyframes bs-breatheSlow { 0%,100%{transform:scale(1)} 50%{transform:scale(1.025)} }
@keyframes bs-glow { 0%,100%{opacity:.3;transform:scale(.88)} 50%{opacity:.65;transform:scale(1.12)} }
@keyframes bs-glowSoft { 0%,100%{opacity:.18;transform:scale(.9)} 50%{opacity:.34;transform:scale(1.05)} }
@keyframes bs-tilt { 0%,100%{transform:rotate(-4deg)} 50%{transform:rotate(4deg)} }
@keyframes bs-dot { 0%,100%{transform:translateY(0);opacity:.35} 50%{transform:translateY(-45%);opacity:1} }
@keyframes bs-scan { 0%,100%{transform:translateX(-1.3px)} 50%{transform:translateX(1.3px)} }
@keyframes bs-nudge { 0%,100%{transform:translate(0,0) rotate(0)} 25%{transform:translate(6%,-6%) rotate(-8deg)} 50%{transform:translate(0,6%) rotate(0)} 75%{transform:translate(-6%,0) rotate(8deg)} }
@keyframes bs-pop { 0%,100%{transform:scale(.82);opacity:.6} 50%{transform:scale(1.08);opacity:1} }
@keyframes bs-flip { 0%,42%{transform:rotate(0)} 58%,100%{transform:rotate(180deg)} }
@keyframes bs-sway { 0%,100%{transform:translateX(-1px) rotate(-1.2deg)} 50%{transform:translateX(1px) rotate(1.2deg)} }
@keyframes bs-bulb { 0%,100%{opacity:.55;transform:translateX(-50%) scale(.9)} 50%{opacity:1;transform:translateX(-50%) scale(1.08)} }
@keyframes bs-rays { 0%,100%{opacity:.15;transform:scale(.85)} 50%{opacity:.7;transform:scale(1.15)} }
@keyframes bs-shake { 0%,68%,100%{transform:translateX(0)} 72%{transform:translateX(-2px)} 78%{transform:translateX(2px)} 84%{transform:translateX(-1.6px)} 90%{transform:translateX(1.4px)} 96%{transform:translateX(0)} }
@keyframes bs-ping { 0%{transform:scale(.9);opacity:.7} 100%{transform:scale(2.4);opacity:0} }
@keyframes bs-check { 0%{stroke-dashoffset:26} 45%,100%{stroke-dashoffset:0} }
@keyframes bs-float { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-6%)} }
`;

const STYLE_ID = "bot-state-keyframes";
function useKeyframes() {
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (document.getElementById(STYLE_ID)) return;
    const el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = KEYFRAMES;
    document.head.appendChild(el);
  }, []);
}

/* Shared SVG props for a Lucide-style glyph. */
const svgBase: React.SVGProps<SVGSVGElement> = {
  viewBox: "0 0 24 24",
  fill: "none",
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

/* Glow disc behind the bot. */
function Glow({ color, anim }: { color: string; anim: string }) {
  return (
    <div
      style={{
        position: "absolute",
        width: "74%",
        height: "74%",
        borderRadius: "50%",
        background: `radial-gradient(circle,${color}55 0%,transparent 70%)`,
        filter: "blur(7px)",
        zIndex: 0,
        animation: anim,
      }}
    />
  );
}

/* Round dark badge in the corner holding a small glyph. */
function Badge({
  color,
  size = "43%",
  right = "-3%",
  bottom = "-3%",
  anim,
  children,
}: {
  color: string;
  size?: string;
  right?: string;
  bottom?: string;
  anim?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        position: "absolute",
        right,
        bottom,
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "50%",
        background: BADGE_BG,
        zIndex: 2,
        animation: anim,
      }}
    >
      {children}
    </div>
  );
}

const bodyBase: React.CSSProperties = {
  width: "80%",
  height: "80%",
  overflow: "visible",
  zIndex: 1,
};

/* Per-variant inner content (glow + bot body + badge). */
function renderVariant(variant: BotStateVariant): React.ReactNode {
  const c = BOT_STATE_COLORS[variant];
  switch (variant) {
    case "available":
      return (
        <>
          <Glow color={c} anim="bs-glow 3.4s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breathe 3.4s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M9 13v2" /><path d="M15 13v2" />
          </svg>
          <div style={{ position: "absolute", right: "3%", bottom: "3%", width: "30%", height: "30%", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2 }}>
            <div style={{ position: "absolute", width: "70%", height: "70%", borderRadius: "50%", background: `${c}77`, animation: "bs-ping 2s var(--bs-ease,ease-out) infinite" }} />
            <div style={{ width: "52%", height: "52%", borderRadius: "50%", background: c, boxShadow: `0 0 0 2.5px ${BADGE_BG}` }} />
          </div>
        </>
      );
    case "thinking":
      return (
        <>
          <Glow color={c} anim="bs-glow 3s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, transformOrigin: "50% 80%", animation: "bs-tilt 3s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M9 13v2" /><path d="M15 13v2" />
          </svg>
          <div style={{ position: "absolute", top: "1%", left: 0, right: 0, display: "flex", justifyContent: "center", alignItems: "flex-end", gap: "8%", height: "20%", zIndex: 2 }}>
            {[0, 0.18, 0.36].map((d, i) => (
              <span key={i} style={{ width: "12%", aspectRatio: "1", borderRadius: "50%", background: c, animation: `bs-dot 1.3s ${d ? "ease-in-out" : "var(--bs-ease,ease-in-out)"} ${d}s infinite` }} />
            ))}
          </div>
        </>
      );
    case "reading":
      return (
        <>
          <Glow color={c} anim="bs-glowSoft 3.2s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breatheSlow 3.6s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" />
            <g style={{ transformOrigin: "center", animation: "bs-scan 1.9s var(--bs-ease,ease-in-out) infinite" }}>
              <path d="M9 13v2" /><path d="M15 13v2" />
            </g>
          </svg>
          <Badge color={c} size="42%" right="-2%" bottom="-2%" anim="bs-float 2.6s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "64%", height: "64%" }}>
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
          </Badge>
        </>
      );
    case "searching":
      return (
        <>
          <Glow color={c} anim="bs-glowSoft 3s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breatheSlow 3.4s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M9 13v2" /><path d="M15 13v2" />
          </svg>
          <Badge color={c} size="44%" right="-4%" bottom="-4%" anim="bs-nudge 2.8s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2.2} style={{ width: "62%", height: "62%" }}>
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
            </svg>
          </Badge>
        </>
      );
    case "responding":
      return (
        <>
          <Glow color={c} anim="bs-glow 2.4s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breathe 2.4s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M9 13v2" /><path d="M15 13v2" />
          </svg>
          <Badge color={c} anim="bs-pop 1.8s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "64%", height: "64%" }}>
              <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
            </svg>
          </Badge>
        </>
      );
    case "slow":
      return (
        <>
          <Glow color={c} anim="bs-glowSoft 4s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, transformOrigin: "50% 80%", animation: "bs-sway 4.2s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M9 13v2" /><path d="M15 13v2" />
          </svg>
          <Badge color={c} size="42%" anim="bs-flip 3.6s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "60%", height: "60%" }}>
              <path d="M5 22h14" /><path d="M5 2h14" /><path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22" /><path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2" />
            </svg>
          </Badge>
        </>
      );
    case "idea":
      return (
        <>
          <Glow color={c} anim="bs-glow 2.6s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breathe 2.6s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" />
            <path d="M8.4 14.4a1.6 1.6 0 0 1 2.4 0" /><path d="M13.2 14.4a1.6 1.6 0 0 1 2.4 0" />
          </svg>
          <div style={{ position: "absolute", top: "-16%", left: "50%", transform: "translateX(-50%)", width: "36%", height: "36%", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2, animation: "bs-bulb 2.4s var(--bs-ease,ease-in-out) infinite" }}>
            <div style={{ position: "absolute", width: "120%", height: "120%", borderRadius: "50%", background: `radial-gradient(circle,${c}aa 0%,transparent 65%)`, animation: "bs-rays 2.4s var(--bs-ease,ease-in-out) infinite" }} />
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "78%", height: "78%", position: "relative" }}>
              <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" /><path d="M9 18h6" /><path d="M10 22h4" />
            </svg>
          </div>
        </>
      );
    case "complete":
      return (
        <>
          <Glow color={c} anim="bs-glow 3s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breathe 3s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" />
            <path d="M8.4 14.4a1.6 1.6 0 0 1 2.4 0" /><path d="M13.2 14.4a1.6 1.6 0 0 1 2.4 0" />
          </svg>
          <Badge color={c}>
            <svg {...svgBase} stroke={c} strokeWidth={2.4} style={{ width: "64%", height: "64%" }}>
              <path d="M20 6 9 17l-5-5" style={{ strokeDasharray: 26, strokeDashoffset: 26, animation: "bs-check 2.6s var(--bs-ease,ease-in-out) infinite" }} />
            </svg>
          </Badge>
        </>
      );
    case "error":
      return (
        <>
          <Glow color={c} anim="bs-glow 1.6s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-shake 2.4s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" />
            <path d="m8 13 2 2" /><path d="m10 13-2 2" /><path d="m14 13 2 2" /><path d="m16 13-2 2" />
          </svg>
          <Badge color={c} anim="bs-pop 1.4s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "64%", height: "64%" }}>
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" />
            </svg>
          </Badge>
        </>
      );
    case "idle":
      return (
        <>
          <Glow color="#94a3b8" anim="bs-glowSoft 5s var(--bs-ease,ease-in-out) infinite" />
          <svg {...svgBase} stroke={c} style={{ ...bodyBase, animation: "bs-breatheSlow 5s var(--bs-ease,ease-in-out) infinite" }}>
            <path d="M12 8V4H8" /><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" />
            <path d="M8 14h2" /><path d="M14 14h2" />
          </svg>
          <Badge color={c} size="40%" right="-2%" bottom="-2%" anim="bs-float 4s var(--bs-ease,ease-in-out) infinite">
            <svg {...svgBase} stroke={c} strokeWidth={2} style={{ width: "60%", height: "60%" }}>
              <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9" />
            </svg>
          </Badge>
        </>
      );
  }
}

export function BotState({
  variant,
  size = 84,
  ease,
  stroke,
  className,
  style,
}: BotStateProps) {
  useKeyframes();

  const wrapperVars = {
    ...(ease ? { ["--bs-ease" as any]: ease } : {}),
    ["--bs-stroke" as any]: stroke ?? 1.8,
  } as React.CSSProperties;

  return (
    <div
      className={className}
      style={{
        width: typeof size === "number" ? `${size}px` : size,
        height: typeof size === "number" ? `${size}px` : size,
        ...style,
      }}
    >
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          strokeWidth: "var(--bs-stroke,1.8)",
          ...(variant === "idle" ? { opacity: 0.62 } : {}),
          ...wrapperVars,
        }}
      >
        {renderVariant(variant)}
      </div>
    </div>
  );
}

export default BotState;

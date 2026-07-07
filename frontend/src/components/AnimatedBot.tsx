import { Bot } from "lucide-react";

// Whimsical "thinking" state for the Bot icon wherever the app is actively
// querying the LLM — a gentle rotation plus two staggered sparks near the
// head. Pure CSS (see index.css), no new asset. Falls back to a plain static
// Bot when `active` is false, so callers can swap in place of a bare <Bot/>.
export default function AnimatedBot({ active, size = 15, className = "" }: {
  active: boolean;
  size?: number;
  className?: string;
}) {
  if (!active) return <Bot size={size} className={className} />;
  return (
    <span className="relative inline-flex" style={{ width: size, height: size }}>
      <Bot size={size} className={`bot-thinking ${className}`} />
      <span className="bot-spark absolute -top-0.5 -left-0.5 w-1 h-1 rounded-full bg-brand-light" />
      <span className="bot-spark bot-spark-2 absolute -top-0.5 -right-0.5 w-1 h-1 rounded-full bg-brand-light" />
    </span>
  );
}

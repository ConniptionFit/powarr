export default function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.9 ? "bg-green-900/60 text-green-300" :
    value >= 0.7 ? "bg-yellow-900/60 text-yellow-300" :
    "bg-red-900/60 text-red-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${color}`}>{pct}%</span>;
}

/**
 * Skeleton loader component for loading states (UX-03).
 * Displays animated placeholder boxes while content loads.
 */

interface SkeletonProps {
  className?: string;
  count?: number;
}

export function Skeleton({ className = "h-12 w-full", count = 1 }: SkeletonProps) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={`${className} bg-gradient-to-r from-slate-700/40 via-slate-600/40 to-slate-700/40 rounded animate-pulse`}
        />
      ))}
    </>
  );
}

export function SkeletonGrid({ cols = 4, rows = 2 }: { cols?: number; rows?: number }) {
  return (
    <div className={`grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-${cols} gap-4`}>
      {Array.from({ length: cols * rows }).map((_, i) => (
        <div key={i} className="bg-surface-raised rounded-xl border border-purple-900/30 p-5">
          <Skeleton className="h-4 w-1/3 mb-4" />
          <Skeleton className="h-8 w-2/3 mb-2" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 5, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex gap-4 px-4 py-3">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={`h-${i}`} className="h-4 flex-1" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div key={`r-${i}`} className="flex gap-4 px-4 py-3 border-b border-purple-900/20">
          {Array.from({ length: cols }).map((_, j) => (
            <Skeleton key={`c-${j}`} className="h-4 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

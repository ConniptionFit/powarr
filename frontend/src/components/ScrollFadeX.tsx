import { useEffect, useRef, useState } from "react";

/**
 * Wraps a horizontally-scrollable region (a wide table on a narrow viewport)
 * with a right-edge fade that appears only while there's more content to
 * scroll to, and disappears once scrolled to the end — the "there's more,
 * swipe me" affordance these tables were missing on mobile. `fadeFrom` must
 * match the scroll container's own background so the gradient blends in.
 */
export default function ScrollFadeX({
  className = "",
  fadeFrom = "from-surface-raised",
  children,
}: {
  className?: string;
  fadeFrom?: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setOverflowing(el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [children]);

  return (
    <div className="relative">
      <div ref={ref} className={className}>
        {children}
      </div>
      {overflowing && (
        <div className={`pointer-events-none absolute top-0 right-0 bottom-0 w-8 bg-gradient-to-l ${fadeFrom} to-transparent`} />
      )}
    </div>
  );
}

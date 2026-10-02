"use client";

import { useEffect, useRef, useState } from "react";
import styles from "../../app/negotiations/negotiations.module.css";
import { rail, type Bubble, type RailPoint } from "../../lib/views/negotiations.ts";

const H = 120;
const PAD = { l: 34, r: 40, t: 10, b: 18 };

const path = (pts: RailPoint[]) => pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");

export function PriceRail({ bubbles, threadId, theirLabel, ourLabel }: {
  bubbles: Bubble[]; threadId: number; theirLabel: string; ourLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(480);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(220, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const r = rail(bubbles, { w, h: H, pad: PAD });
  const empty = !r.us.length && !r.them.length;
  const series: [string, string, string, RailPoint[]][] = [
    ["them", styles.lineThem, styles.dotThem, r.them],
    ["us", styles.lineUs, styles.dotUs, r.us],
  ];
  return (
    <div className={styles.railWrap} ref={ref}>
      {!empty && (
        <>
          <div className={styles.legend}>
            <span className={styles.them}>their {theirLabel}</span>
            <span className={styles.us}>our {ourLabel}</span>
          </div>
          <svg className={styles.rail} viewBox={`0 0 ${w} ${H}`} width={w} height={H} role="img" aria-label={`price rail of thread ${threadId}`}>
            {r.grid.map((g) => (
              <g key={g.label}>
                <line className={styles.grid} x1={PAD.l} x2={w - PAD.r} y1={g.y} y2={g.y} />
                <text x={PAD.l - 4} y={g.y + 3} textAnchor="end">{g.label}</text>
              </g>
            ))}
            <text x={PAD.l} y={H - 3}>rounds →</text>
            {series.map(([side, line, dot, pts]) => pts.length > 0 && (
              <g key={side}>
                <path className={line} d={path(pts)} />
                {pts.map((p) => (
                  <circle key={p.eventId} className={`${dot}${p.final ? ` ${styles.dotFinal}` : ""}`} cx={p.x} cy={p.y} r={p.final ? 5 : 3.5}>
                    <title>{`r${p.round} · ${side} ${p.price} P${p.final ? " · FINAL" : ""}`}</title>
                  </circle>
                ))}
                <text x={pts[pts.length - 1].x + 8} y={pts[pts.length - 1].y + 3}>{pts[pts.length - 1].price}</text>
              </g>
            ))}
          </svg>
        </>
      )}
    </div>
  );
}

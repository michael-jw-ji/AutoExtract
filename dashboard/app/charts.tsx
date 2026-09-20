"use client";

/**
 * Chart primitives, hand-rolled — no chart library, nothing fetched at runtime.
 *
 * Palette is the validated default for THIS dashboard's light surface
 * (#fcfcfb), run through the skill's validator against that exact surface:
 *   lightness PASS · chroma PASS · CVD worst ΔE 9.2 · normal-vision 24.0
 *   contrast WARN: aqua is 2.74:1, so the RELIEF RULE applies -- it may only
 *   be used where a visible label or a table view carries the identity. The
 *   funnel legend prints counts and every chart has a data-table toggle,
 *   which satisfies it. Do not use aqua on an unlabelled mark.
 * Do not add a 4th categorical hue without re-running the validator.
 *
 * Magnitude charts use ONE hue and let length carry the value. Colour is only
 * spent where identity is the point (the repair funnel).
 */

import { useEffect, useRef, useState } from "react";

export const VIZ = {
  s1: "#2a78d6", // blue   — categorical slot 1
  s2: "#eb6834", // orange — slot 2
  s3: "#1baf7a", // aqua   — slot 3
  good: "#0ca30c",
  critical: "#d03b3b",
  warning: "#b07c00",
  grid: "#ededf0",
  baseline: "#c9c9ce",
  muted: "#8e8e97",
  surface: "#ffffff",
} as const;

/* ------------------------------------------------------------------ tooltip */

type TipState = { x: number; y: number; rows: [string, string][]; title: string } | null;

export function useTip() {
  const [tip, setTip] = useState<TipState>(null);
  const show = (e: React.MouseEvent, title: string, rows: [string, string][]) =>
    setTip({ x: e.clientX, y: e.clientY, title, rows });
  const hide = () => setTip(null);
  return { tip, show, hide };
}

export function Tip({ tip }: { tip: TipState }) {
  if (!tip) return null;
  const style: React.CSSProperties = {
    left: Math.min(tip.x + 14, (typeof window !== "undefined" ? window.innerWidth : 1200) - 300),
    top: tip.y + 14,
  };
  return (
    <div className="tip" style={style} role="tooltip">
      <div className="tip-title">{tip.title}</div>
      {tip.rows.map(([k, v]) => (
        <div className="tip-row" key={k}>
          <span>{k}</span>
          <b>{v}</b>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ horizontal bar */

export type BarDatum = {
  key: string;
  label: string;
  value: number;
  sub?: string;
  accent?: string;
};

export function HBar({
  data,
  unit = "",
  onSelect,
  selected,
  format,
  domainMax,
}: {
  data: BarDatum[];
  unit?: string;
  onSelect?: (key: string) => void;
  selected?: string | null;
  format?: (v: number) => string;
  /** Fixed axis maximum. REQUIRED for percentages: scaling to the largest
   *  value makes 81% render as a full bar, which overstates it. Counts may
   *  scale to their own max, because there the bar length is relative by
   *  nature and there is no implied ceiling. */
  domainMax?: number;
}) {
  const { tip, show, hide } = useTip();
  const max = domainMax ?? Math.max(1, ...data.map((d) => d.value));
  const fmt = format ?? ((v: number) => `${v}${unit}`);

  return (
    <>
      <div className="hbar">
        {data.map((d) => {
          const pct = (d.value / max) * 100;
          const active = selected === d.key;
          return (
            <div
              key={d.key}
              className={`hbar-row${onSelect ? " clickable" : ""}${active ? " active" : ""}`}
              onMouseMove={(e) =>
                show(e, d.label, [
                  ["value", fmt(d.value)],
                  ["share", `${((d.value / data.reduce((s, x) => s + x.value, 0)) * 100).toFixed(1)}%`],
                  ...(d.sub ? ([["detail", d.sub]] as [string, string][]) : []),
                ])
              }
              onMouseLeave={hide}
              onClick={() => onSelect?.(d.key)}
              role={onSelect ? "button" : undefined}
              tabIndex={onSelect ? 0 : undefined}
              onKeyDown={(e) => e.key === "Enter" && onSelect?.(d.key)}
            >
              <div className="hbar-val">{fmt(d.value)}</div>
              <div className="hbar-track">
                <div
                  className="hbar-fill"
                  style={{ width: `${Math.max(pct, 1.5)}%`, background: d.accent ?? VIZ.s1 }}
                />
                <div className="hbar-label">{d.label}</div>
              </div>
            </div>
          );
        })}
      </div>
      <Tip tip={tip} />
    </>
  );
}

/* ----------------------------------------------------------------- trendline */

export function TrendLine({
  points,
  height = 150,
}: {
  points: { i: number; value: number; label: string }[];
  height?: number;
}) {
  const { tip, show, hide } = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const W = 640;
  const H = height;
  const PAD = { l: 34, r: 10, t: 12, b: 20 };

  if (points.length < 2) {
    return <div className="empty">not enough data yet — needs at least two points</div>;
  }

  const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - v) * (H - PAD.t - PAD.b);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join("");

  const onMove = (e: React.MouseEvent) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const rel = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((rel - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1));
    const idx = Math.max(0, Math.min(points.length - 1, i));
    setHover(idx);
    show(e, points[idx].label, [["valid rate", `${(points[idx].value * 100).toFixed(1)}%`]]);
  };

  return (
    <>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        className="chart"
        onMouseMove={onMove}
        onMouseLeave={() => {
          setHover(null);
          hide();
        }}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={PAD.l}
              x2={W - PAD.r}
              y1={y(t)}
              y2={y(t)}
              stroke={VIZ.grid}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            <text x={PAD.l - 7} y={y(t) + 3} className="tick" textAnchor="end">
              {t * 100}%
            </text>
          </g>
        ))}
        {/* No area fill. This rate lives in its top decile, so filling down to
            zero paints most of the panel a solid colour that encodes nothing
            and buries the gridlines. The axis stays 0-100% -- truncating it to
            90-100% would magnify noise into apparent movement. */}
        <path
          d={path}
          fill="none"
          stroke={VIZ.s1}
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        {hover !== null && (
          <>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.t}
              y2={H - PAD.b}
              stroke={VIZ.muted}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            <circle cx={x(hover)} cy={y(points[hover].value)} r={5} fill={VIZ.s1}
                    stroke={VIZ.surface} strokeWidth={2} />
          </>
        )}
      </svg>
      <Tip tip={tip} />
    </>
  );
}

/* -------------------------------------------------------------------- funnel */

export function Funnel({
  stages,
}: {
  stages: { label: string; value: number; color: string }[];
}) {
  const { tip, show, hide } = useTip();
  const total = stages.reduce((s, x) => s + x.value, 0);
  if (!total) return <div className="empty">no repairs yet</div>;

  return (
    <>
      <div className="funnel">
        {stages.map((s) => (
          <div
            key={s.label}
            className="funnel-seg"
            style={{ flexGrow: Math.max(s.value, 0.001), background: s.color }}
            onMouseMove={(e) =>
              show(e, s.label, [
                ["count", String(s.value)],
                ["share", `${((s.value / total) * 100).toFixed(1)}%`],
              ])
            }
            onMouseLeave={hide}
          >
            {s.value / total > 0.09 ? s.value : ""}
          </div>
        ))}
      </div>
      <div className="legend">
        {stages.map((s) => (
          <span key={s.label} className="legend-item">
            <i style={{ background: s.color }} />
            {s.label} <b>{s.value}</b>
          </span>
        ))}
      </div>
      <Tip tip={tip} />
    </>
  );
}

/* --------------------------------------------------------------- table view */

export function TableView({
  headers,
  rows,
  open,
  onToggle,
}: {
  headers: string[];
  rows: (string | number)[][];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="tableview">
      <button className="linkbtn" onClick={onToggle}>
        {open ? "hide data table" : "show data table"}
      </button>
      {open && (
        <table className="mini">
          <thead>
            <tr>
              {headers.map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function useNow(ms: number) {
  const [, set] = useState(0);
  useEffect(() => {
    const id = setInterval(() => set((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

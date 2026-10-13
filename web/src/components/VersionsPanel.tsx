import { f1, pct, pts } from '../format'
import type { SiteData } from '../types'

const LO = 0.8
const HI = 1.0
const height = (x: number) => Math.max(0, Math.min(100, ((x - LO) / (HI - LO)) * 100))

export function VersionsPanel({ data }: { data: SiteData }) {
  const points = [
    { name: data.base.name, after: data.base.f1, kind: 'base' },
    ...data.versions.map((v) => ({ name: v.name, after: v.after, kind: v.decision })),
  ]
  return (
    <>
      <div className="panel-header">
        <span className="title">FIELD F1 PER VERSION</span>
        <span>SCALE 0.80 TO 1.00 · OLDEST LEFT</span>
      </div>
      <div className="chart">
        <div className="bars">
          {points.map((p) => (
            <div key={p.name} className={`bar ${p.kind}`} style={{ height: `${height(p.after)}%` }} title={`${p.name}: ${f1(p.after)}`}>
              <span>{p.after.toFixed(3)}</span>
            </div>
          ))}
        </div>
        <div className="bar-labels">
          {points.map((p) => (
            <div key={p.name}>{p.name}</div>
          ))}
        </div>
        <div className="legend">
          <span><i className="base" />BASELINE</span>
          <span><i />PROMOTED</span>
          <span><i className="rejected" />REJECTED</span>
        </div>
      </div>

      <div className="panel-header">
        <span className="title">EVERY GATE DECISION</span>
        <span>NEWEST FIRST · {data.versions.length} DECISIONS</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>What changed · why the gate decided</th>
              <th className="num">F1 before → after</th>
              <th className="num">Gain pts</th>
              <th className="num">Valid</th>
              <th>Gate</th>
            </tr>
          </thead>
          <tbody>
            {[...data.versions].reverse().map((v) => (
              <tr key={v.name} className={v.name === data.current.name ? 'current' : undefined}>
                <td className="mono">
                  {v.name}
                  <div className="why">{v.date}</div>
                </td>
                <td>
                  {v.note}
                  <div className="why">{v.reason}</div>
                </td>
                <td className="num">{f1(v.before)} → {f1(v.after)}</td>
                <td className={`num${v.gain_pp < 0 ? ' neg' : ''}`}>{pts(v.gain_pp)}</td>
                <td className="num">{pct(v.valid)}</td>
                <td>
                  <span className={`badge ${v.decision === 'promoted' ? 'solid' : 'red'}`}>{v.decision}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

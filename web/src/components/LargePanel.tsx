import { f1, pct, pts } from '../format'
import type { SiteData } from '../types'
import { Metric } from './Metric'

export function LargePanel({ data }: { data: SiteData }) {
  const L = data.large
  if (!L) return <div className="callout neutral">No 32B comparison has been recorded.</div>
  const total = Object.values(L.outcomes).reduce((a, b) => a + b, 0)
  return (
    <>
      <div className="grid-3">
        <Metric label={`7B, prompt only (${L.small_name})`} value={f1(L.small_f1)} hint={`VALID ${pct(L.small_valid)}`} />
        <Metric label={`32B, ${L.small_name} pipeline`} value={f1(L.f1)} hint={`VALID ${pct(L.valid)} · H100`} />
        <Metric label={`7B + LoRA (${data.current.name})`} value={f1(data.current.f1)} hint={`VALID ${pct(data.current.valid)} · L40S`} accent />
      </div>
      <div className="callout">
        The LoRA, trained only on the 7B's own verified failures, brings the 7B level with a model more than four times
        its size. The 32B beat the prompt-only 7B by {pts(L.ci_pp.delta)} points (95% interval {pts(L.ci_pp.lo)} to{' '}
        {pts(L.ci_pp.hi)}).
      </div>
      <div className="panel-header">
        <span className="title">FILING BY FILING, 32B VS {L.small_name.toUpperCase()}</span>
        <span>{total} FILINGS</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Outcome</th><th className="num">Filings</th></tr>
          </thead>
          <tbody>
            {Object.entries(L.outcomes).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

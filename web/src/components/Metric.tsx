export function Metric({ label, value, hint, accent }: { label: string; value: string; hint: string; accent?: boolean }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={`value${accent ? ' accent' : ''}`}>{value}</div>
      <div className="hint">{hint}</div>
    </div>
  )
}

import { useState, type ReactNode } from 'react'
import { f1, pct, pts } from './format'
import type { SiteData } from './types'
import { Metric } from './components/Metric'
import { VersionsPanel } from './components/VersionsPanel'
import { FieldsPanel } from './components/FieldsPanel'
import { LargePanel } from './components/LargePanel'
import { WorkflowPanel } from './components/WorkflowPanel'
import { MethodPanel } from './components/MethodPanel'

type Tab = 'workflow' | 'versions' | 'fields' | 'large' | 'method'

const TABS: { id: Tab; label: string; count?: (d: SiteData) => number; render: (d: SiteData) => ReactNode }[] = [
  { id: 'workflow', label: 'WORKFLOW', render: (d) => <WorkflowPanel data={d} /> },
  { id: 'versions', label: 'VERSIONS', count: (d) => d.versions.length, render: (d) => <VersionsPanel data={d} /> },
  { id: 'fields', label: 'FIELDS', count: (d) => d.fields.length, render: (d) => <FieldsPanel data={d} /> },
  { id: 'large', label: 'VERSUS 32B', render: (d) => <LargePanel data={d} /> },
  { id: 'method', label: 'METHOD', render: (d) => <MethodPanel data={d} /> },
]

const fromHash = (): Tab => TABS.find((t) => t.id === location.hash.slice(1))?.id ?? 'workflow'

export default function App({ data }: { data: SiteData }) {
  const [tab, setTab] = useState<Tab>(fromHash)
  const open = (id: Tab) => {
    setTab(id)
    history.replaceState(null, '', `#${id}`)
  }
  const c = data.cost

  return (
    <div className="page">
      <header className="nav">
        <div className="brand">
          INSIDER READING<span className="sq">■</span>
        </div>
        <div className="nav-meta">
          <span>QWEN2.5-7B · LORA · MODAL</span>
          <span className="status">
            <span className="dot" />
            HOLDOUT {data.holdout.documents} · {data.holdout.hash}
          </span>
        </div>
      </header>

      <div className="tag">SEC FORM 4 · INSIDER TRANSACTIONS</div>
      <h1 className="headline">Read insider filings. Check every field against the XML.</h1>
      <p className="sub">
        A 7B open model reads each Form 4 page and returns its transactions as JSON. Every number here is graded against
        the filing's own XML on a frozen holdout, and a change ships only if a statistical gate says it helped.
      </p>

      <section className="metrics" aria-label="Summary">
        <Metric
          label="Field F1"
          value={f1(data.current.f1)}
          hint={`${data.base.name.toUpperCase()} ${f1(data.base.f1)} · ${pts((data.current.f1 - data.base.f1) * 100)} PTS`}
          accent
        />
        <Metric label="Valid" value={pct(data.current.valid)} hint={`${data.holdout.documents} FILINGS · SCHEMA + RULES`} />
        <Metric label="Cost per filing" value={c ? `$${c.usd_per_filing.toFixed(5)}` : '—'} hint={c ? `GPU TIME · ${c.gpu}` : 'NO TRACES'} />
        <Metric label="Latency p50" value={c ? `${c.p50_s} s` : '—'} hint={c ? `P99 ${c.p99_s} S` : 'NO TRACES'} />
      </section>

      <nav className="steps" role="tablist" aria-label="Sections">
        {TABS.map((t, i) => (
          <button key={t.id} type="button" role="tab" className="step" aria-selected={tab === t.id} onClick={() => open(t.id)}>
            <span className="num">{String(i + 1).padStart(2, '0')}</span>
            <span>
              {t.label}
              {t.count && <span className="count"> · {t.count(data)}</span>}
            </span>
          </button>
        ))}
      </nav>

      <main role="tabpanel">{TABS.find((t) => t.id === tab)!.render(data)}</main>

      <footer className="footer">
        <span>GOLD = EACH FILING'S OWN XML · NEVER A MODEL</span>
        <span>
          BUILT FROM results/runs/ · {data.current.name} @ {data.current.commit}
        </span>
      </footer>
    </div>
  )
}

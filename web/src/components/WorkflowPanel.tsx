import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { f1 } from '../format'
import type { SiteData } from '../types'
import { Icon, type IconName } from './icons'

// Layout: a 6-column grid in SVG units. Row "mid" is what runs for every
// filing; row "bot" is the improvement loop, read right to left.
const W = 150
const H = 64
const col = (i: number) => 15 + i * 180
const ROW = { top: 20, mid: 150, bot: 300, rej: 410 }

interface Node {
  id: IconName
  label: string
  sub: string
  x: number
  y: number
  kind?: 'key' | 'ship' | 'reject'
  file: string
  body: (d: SiteData) => ReactNode
}

const NODES: Node[] = [
  {
    id: 'xml', label: 'XML GOLD', sub: 'the answer key', x: col(0), y: ROW.top, kind: 'key', file: 'domains/form4_io.py',
    body: () => "Every Form 4 on EDGAR ships with a machine-readable XML version. It is parsed into the gold answer, so no model ever grades itself.",
  },
  {
    id: 'fetch', label: 'FETCH', sub: 'EDGAR filing', x: col(0), y: ROW.mid, file: 'scripts/fetch_form4.py',
    body: () => 'Downloads the filing folder: the page a person reads, and the XML behind it. At most 10 requests a second, with a contact User-Agent, as SEC asks.',
  },
  {
    id: 'render', label: 'RENDER', sub: 'page to text', x: col(1), y: ROW.mid, file: 'domains/form4_io.py · render v3',
    body: () => 'Turns the HTML page into plain text. Each row is labelled as a transaction or a holding, and footnote markers are split from the values they are glued to.',
  },
  {
    id: 'read', label: 'READ', sub: '7B + LoRA', x: col(2), y: ROW.mid, file: 'serve/ · prompt v2 · form4-lora-v1',
    body: (d) => `Qwen2.5-7B with the LoRA adapter, on vLLM on a Modal L40S, reads the text and returns the transactions as JSON. Current pipeline: ${d.current.name}, field F1 ${f1(d.current.f1)}.`,
  },
  {
    id: 'post', label: 'POST-PROCESS', sub: 'enrich v4', x: col(3), y: ROW.mid, file: 'domains/form4.py',
    body: () => 'Code fills in what the page states exactly, so the model never has to: the owner boxes, value = shares × price, and prices that are only a footnote marker.',
  },
  {
    id: 'validate', label: 'VALIDATE', sub: 'schema + rules', x: col(4), y: ROW.mid, file: 'verify/',
    body: () => 'A pydantic schema plus explicit rules: transaction codes, acquired or disposed, and the owned-after chain between rows. Deterministic code, never an LLM.',
  },
  {
    id: 'grade', label: 'GRADE', sub: 'field by field', x: col(5), y: ROW.mid, file: 'evalgate/scorer.py · verify/compare.py',
    body: () => "Compares the answer with the XML gold field by field, with rows aligned by best overlap. Micro F1 over all fields is the headline number.",
  },
  {
    id: 'failures', label: 'FAILURES', sub: 'root-caused', x: col(5), y: ROW.bot, file: 'make failures · FAILURES.md',
    body: () => 'Every filing with a wrong field is read and root-caused row by row. That is where each fix below came from: 162 failing filings at the start.',
  },
  {
    id: 'change', label: 'CHANGE', sub: 'one layer', x: col(4), y: ROW.bot, file: 'one candidate at a time',
    body: () => 'Fix the cause in the layer it lives in: the prompt, the rendering, the post-processing, or the weights. A change after the model replays recorded answers with no model calls; anything else calls the model again.',
  },
  {
    id: 'score', label: 'SCORE', sub: 'frozen holdout', x: col(3), y: ROW.bot, file: 'scripts/run_cycle.py',
    body: (d) => `The candidate runs on the frozen holdout: ${d.holdout.documents} filings, hash ${d.holdout.hash}. They never appear in a prompt or in training data. Every answer is recorded, and a missing answer is never scored as wrong.`,
  },
  {
    id: 'gate', label: 'GATE', sub: 'bootstrap 95%', x: col(2), y: ROW.bot, file: 'evalgate/gate.py',
    body: (d) => {
      const promoted = d.versions.filter((v) => v.decision === 'promoted').length
      return `Promote only if the paired-bootstrap 95% interval of the F1 gain is above zero, no field drops more than 2 points, and the valid rate holds. So far: ${promoted} promoted, ${d.versions.length - promoted} rejected.`
    },
  },
  {
    id: 'ship', label: 'SHIP', sub: 'interval > 0', x: col(1), y: ROW.bot, kind: 'ship', file: 'promotions · data/ci/baseline.json',
    body: () => 'The candidate becomes the pipeline that serves, and the CI baseline is updated. From then on every pull request replays 30 recorded answers and fails on any field drop over 0.5 points.',
  },
  {
    id: 'reject', label: 'REJECT', sub: 'interval has 0', x: col(2), y: ROW.rej, kind: 'reject', file: 'promotions',
    body: () => 'The gain may be noise, so the change does not ship. The decision is still recorded with its reason, and past decisions are never re-made under a rule chosen later.',
  },
]

interface Edge {
  d: string
  kind: 'path' | 'loop' | 'key' | 'off'
  label?: { x: number; y: number; text: string }
}

const mid = H / 2
const EDGES: Edge[] = [
  { d: `M${col(0) + W / 2} ${ROW.mid} V${ROW.top + H}`, kind: 'key' },
  { d: `M${col(0) + W} ${ROW.top + mid} H${col(5) + W / 2} V${ROW.mid}`, kind: 'key', label: { x: col(2) + 75, y: ROW.top + mid - 6, text: 'ANSWER KEY' } },
  ...[0, 1, 2, 3, 4].map((i): Edge => ({ d: `M${col(i) + W} ${ROW.mid + mid} H${col(i + 1)}`, kind: 'path' })),
  { d: `M${col(5) + W / 2} ${ROW.mid + H} V${ROW.bot}`, kind: 'loop', label: { x: col(5) + W / 2 + 8, y: 262, text: 'WRONG FIELDS' } },
  ...[5, 4, 3, 2].map((i): Edge => ({ d: `M${col(i)} ${ROW.bot + mid} H${col(i - 1) + W}`, kind: 'loop' })),
  { d: `M${col(1) + W / 2} ${ROW.bot} V257 H${col(2) + W / 2} V${ROW.mid + H}`, kind: 'loop', label: { x: col(1) + W / 2 + 8, y: 250, text: 'SERVES NEXT' } },
  { d: `M${col(2) + W / 2} ${ROW.bot + H} V${ROW.rej}`, kind: 'off' },
]

function GraphNode({ n, selected, onSelect }: { n: Node; selected: boolean; onSelect: () => void }) {
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect()
    }
  }
  return (
    <g
      className={`node${n.kind ? ` ${n.kind}` : ''}${selected ? ' sel' : ''}`}
      transform={`translate(${n.x} ${n.y})`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${n.label}: ${n.sub}`}
      onClick={onSelect}
      onKeyDown={onKey}
    >
      <rect width={W} height={H} />
      <g transform="translate(11 17) scale(1.25)">
        <Icon name={n.id} />
      </g>
      <text className="lbl" x={50} y={28}>{n.label}</text>
      <text className="sub" x={50} y={44}>{n.sub}</text>
    </g>
  )
}

export function WorkflowPanel({ data }: { data: SiteData }) {
  const [selected, setSelected] = useState<IconName>('read')
  const node = NODES.find((n) => n.id === selected)!

  return (
    <>
      <div className="panel-header">
        <span className="title">HOW IT WORKS</span>
        <span>CLICK A STEP</span>
      </div>
      <div className="graph">
        <svg viewBox="0 0 1080 485" role="group" aria-label="Pipeline and improvement loop">
          <defs>
            {(['path', 'loop', 'key', 'off'] as const).map((k) => (
              <marker key={k} id={`arrow-${k}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path className={`head ${k}`} d="M0 0L8 4L0 8z" />
              </marker>
            ))}
          </defs>
          {EDGES.map((e, i) => (
            <g key={i}>
              <path className={`edge ${e.kind}`} d={e.d} markerEnd={`url(#arrow-${e.kind})`} />
              {e.label && <text className="edge-label" x={e.label.x} y={e.label.y}>{e.label.text}</text>}
            </g>
          ))}
          <text className="row-label" x={col(1)} y={ROW.mid - 12}>EVERY FILING</text>
          <text className="row-label loop" x={col(5) + W} y={ROW.bot + H + 22} textAnchor="end">THE IMPROVEMENT LOOP</text>
          {NODES.map((n) => (
            <GraphNode key={n.id} n={n} selected={n.id === selected} onSelect={() => setSelected(n.id)} />
          ))}
        </svg>
      </div>
      <div className="legend graph-legend">
        <span><i className="line" />EVERY FILING</span>
        <span><i className="line loop" />IMPROVEMENT LOOP</span>
        <span><i className="line key" />ANSWER KEY</span>
      </div>

      <div className="detail">
        <svg className="detail-icon" viewBox="0 0 24 24" aria-hidden="true">
          <Icon name={node.id} />
        </svg>
        <div>
          <div className="label">
            <span className="num">{node.label}</span> · {node.sub}
          </div>
          <p>{node.body(data)}</p>
          <code>{node.file}</code>
        </div>
      </div>
    </>
  )
}

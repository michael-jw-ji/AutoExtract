import type { ReactNode } from 'react'
import { REPO, f1, repoFile } from '../format'
import type { SiteData } from '../types'

interface FlowStep {
  name: string
  body: ReactNode
  file: string
}

const PIPELINE: FlowStep[] = [
  { name: 'Fetch', body: 'The filing folder from EDGAR: the page a person reads and the XML behind it.', file: 'scripts/fetch_form4.py' },
  { name: 'Render', body: 'Page to plain text. Rows are labelled transaction or holding; footnote markers are split from values.', file: 'domains/form4_io.py · render v3' },
  { name: 'Read', body: 'Qwen2.5-7B with the LoRA, on vLLM on a Modal L40S, returns the transactions as JSON.', file: 'serve/ · prompt v2 · form4-lora-v1' },
  { name: 'Post-process', body: 'Code fills in what the page states exactly: owner boxes, value = shares × price, marker-only prices.', file: 'domains/form4.py · enrich v4' },
  { name: 'Validate', body: 'Pydantic schema plus rules: codes, direction, the owned-after chain. Deterministic, no LLM.', file: 'verify/' },
  { name: 'Grade', body: "Field by field against the filing's own XML.", file: 'evalgate/scorer.py' },
]

const LOOP: FlowStep[] = [
  { name: 'Read failures', body: 'Every failing holdout filing, root-caused row by row.', file: 'make failures · FAILURES.md' },
  { name: 'Change one layer', body: 'The prompt, the rendering, the post-processing, or the weights (LoRA).', file: 'one candidate at a time' },
  { name: 'Score', body: 'The candidate runs on the frozen 189-filing holdout. Every answer is recorded.', file: 'scripts/run_cycle.py' },
  { name: 'Gate', body: 'Promote only if the 95% bootstrap interval of the F1 gain is above zero and nothing else drops.', file: 'evalgate/gate.py' },
  { name: 'Ship or reject', body: 'Both are recorded. A promotion updates the CI baseline that guards every PR.', file: 'promotions · data/ci/' },
]

interface Milestone {
  date: string
  what: string
  body: ReactNode
  versions?: string[]
  origin?: boolean
}

const TIMELINE: Milestone[] = [
  {
    date: '2026-09-19',
    what: 'Hack the North: AutoExtract',
    body: 'A self-improving extractor for synthetic invoices: validate, repair failures, train a LoRA on verified repairs, gate it. +20.10 points against a negative control.',
    origin: true,
  },
  {
    date: '2026-10-11',
    what: 'Real data: SEC Form 4',
    body: 'Fetched 530 filings from EDGAR and parsed each XML as gold (2 dropped for broken gold). Reviewed tricky filings into 44 AI-assisted, human-confirmed labels, then froze a 189-filing, 553-transaction holdout and recorded its hash.',
  },
  {
    date: '2026-10-11',
    what: 'Baseline, then every failure read',
    body: 'The prompt-only 7B: 54.5% valid. All 162 failing filings were root-caused row by row into FAILURES.md, which is where every later fix came from.',
    versions: ['base'],
  },
  {
    date: '2026-10-11',
    what: 'Prompt fixes, and a better gate',
    body: 'Prompt v2 was promoted; v3 was rejected. The fixed +2-point margin turned out to be miscalibrated, so the gate became a paired bootstrap with a per-field check, for future candidates only. Past decisions stand.',
    versions: ['prompt-v2', 'prompt-v3'],
  },
  {
    date: '2026-10-12',
    what: 'Fix it in the right layer',
    body: 'Owner flags moved from the model to code. The big win was the rendering, not the prompt: labelling rows as transaction or holding took F1 from 0.8716 to 0.9182 and valid from 84.1% to 96.3%.',
    versions: ['code-fixes-1', 'render-v2', 'render-v3'],
  },
  {
    date: '2026-10-13',
    what: 'A bigger model, and CI',
    body: (
      <>
        Qwen2.5-32B on the same holdout as a reference, including the failures it does not fix. A CI eval replays 30
        recorded answers on every PR; <a href={`${REPO}/pull/2`}>PR #2</a> is a regression every unit test passes and
        CI catches.
      </>
    ),
  },
  {
    date: '2026-10-13',
    what: 'Small post-processing wins',
    body: 'Footnote markers stripped from titles, and marker-only prices read as null. Both gains are small, and both intervals exclude zero.',
    versions: ['enrich-v3', 'enrich-v4'],
  },
  {
    date: '2026-10-13',
    what: 'Tracing, a judge, an MCP server',
    body: 'OpenTelemetry spans for cost and latency per filing. An LLM judge for officer titles, checked against human-confirmed truth (TPR 0.989, TNR 0.984). Prompt v4 rejected: its interval included zero. An MCP server so AI agents can extract a filing and check it against its XML.',
    versions: ['prompt-v4'],
  },
  {
    date: '2026-10-13',
    what: 'The loop, closed: LoRA v1',
    body: "Served every live filing (never the holdout); each failure's repair is its own XML gold, so every repair is verified. 186 repairs + 80 replays, rank-16 LoRA on Modal. It matches the 32B.",
    versions: ['lora-v1'],
  },
  {
    date: '2026-10-13',
    what: 'Renamed to Insider Reading',
    body: 'Repo, package and Modal apps renamed; the invoice-era code removed.',
  },
]

function Flow({ steps }: { steps: FlowStep[] }) {
  return (
    <div className="flow" style={{ ['--cols' as string]: steps.length }}>
      {steps.map((s, i) => (
        <div key={s.name} className={`flow-step${i === steps.length - 1 ? ' end' : ''}`}>
          <div className="num">{String(i + 1).padStart(2, '0')}</div>
          <div className="name">{s.name}</div>
          <p>{s.body}</p>
          <code>{s.file}</code>
        </div>
      ))}
    </div>
  )
}

function VersionChips({ names, data }: { names: string[]; data: SiteData }) {
  return (
    <div className="badges">
      {names.map((name) => {
        if (name === data.base.name) return <span key={name} className="badge">{name} · {f1(data.base.f1)}</span>
        const v = data.versions.find((x) => x.name === name)
        if (!v) return null
        return (
          <span key={name} className={`badge ${v.decision === 'promoted' ? 'solid' : 'red'}`}>
            {name} · {v.decision} · {f1(v.after)}
          </span>
        )
      })}
    </div>
  )
}

export function WorkflowPanel({ data }: { data: SiteData }) {
  return (
    <>
      <div className="panel-header">
        <span className="title">ONE FILING, END TO END</span>
        <span>WHAT RUNS FOR EVERY FORM 4</span>
      </div>
      <Flow steps={PIPELINE} />

      <div className="panel-header">
        <span className="title">HOW A CHANGE SHIPS</span>
        <span>THE IMPROVEMENT LOOP</span>
      </div>
      <Flow steps={LOOP} />
      <div className="branches">
        <div className="branch">
          <div className="label">Change after the model</div>
          Post-processing is re-run over the incumbent's recorded answers: no model calls, and the score isolates the
          code change.
        </div>
        <div className="branch">
          <div className="label">Change before or in the model</div>
          A prompt, rendering or LoRA change calls the model on all {data.holdout.documents} filings. Answers are
          cached as they arrive, and a missing answer is never scored as a wrong one.
        </div>
      </div>

      <div className="panel-header">
        <span className="title">WHAT I DID</span>
        <span>FROM THE GIT HISTORY · <a href={repoFile('RESULTS.md')}>RESULTS.md</a></span>
      </div>
      <ol className="timeline">
        {TIMELINE.map((m) => (
          <li key={m.what} className={m.origin ? 'origin' : undefined}>
            <div className="date">{m.date}</div>
            <div>
              <div className="what">{m.what}</div>
              <p>{m.body}</p>
              {m.versions && <VersionChips names={m.versions} data={data} />}
            </div>
          </li>
        ))}
      </ol>
    </>
  )
}

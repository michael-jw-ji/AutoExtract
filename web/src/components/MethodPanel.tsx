import { REPO, repoFile } from '../format'
import type { SiteData } from '../types'

export function MethodPanel({ data }: { data: SiteData }) {
  return (
    <>
      <div className="callout">
        <strong>Gold comes from the XML.</strong> Every Form 4 on EDGAR ships with a machine-readable XML version. The
        model reads the rendered page; the XML is the answer key, so no model ever grades itself.
      </div>
      <div className="callout neutral">
        <strong>A frozen holdout.</strong> {data.holdout.documents} filings, id-set hash <code>{data.holdout.hash}</code>.
        They never appear in a prompt or in training data, and code hard-fails if they do.
      </div>
      <div className="callout neutral">
        <strong>A statistical gate.</strong> A change is promoted only if the paired-bootstrap 95% interval of its F1
        gain is above zero, no field drops more than 2 points, and the valid rate does not drop. Rejections are recorded
        too: see the Versions tab.
      </div>
      <div className="callout neutral">
        <strong>CI on every pull request.</strong> 30 recorded answers are replayed through the new code with no model
        calls, and the build fails on any field drop over 0.5 points. <a href={`${REPO}/pull/2`}>PR #2</a> shows it
        catching a regression that every unit test passes.
      </div>
      <div className="callout neutral">
        <strong>Read more:</strong> <a href={repoFile('RESULTS.md')}>RESULTS.md</a> (every number),{' '}
        <a href={repoFile('FAILURES.md')}>FAILURES.md</a> (what went wrong and why),{' '}
        <a href={repoFile('docs/HOW_IT_WORKS.md')}>HOW_IT_WORKS.md</a> (the walkthrough).
      </div>
    </>
  )
}

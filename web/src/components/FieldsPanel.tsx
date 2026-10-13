import { pct, pts, repoFile } from '../format'
import type { SiteData } from '../types'

function FieldName({ path }: { path: string }) {
  const [group, ...rest] = path.split('.*.')
  if (!rest.length) return <>{path}</>
  return (
    <>
      <span className="dim">{group} ·</span> {rest.join('.')}
    </>
  )
}

export function FieldsPanel({ data }: { data: SiteData }) {
  return (
    <>
      <div className="callout neutral">
        Recall of each field in the XML gold: how many of the filing's values the model got exactly right. It ignores
        rows the model invents, which is why <a href={repoFile('RESULTS.md')}>RESULTS.md</a> reports a row table beside it.
      </div>
      <div className="panel-header">
        <span className="title">RECALL PER FIELD</span>
        <span>{data.fields_before} → {data.current.name}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th className="num">{data.fields_before}</th>
              <th className="num">{data.current.name}</th>
              <th className="num">Change pts</th>
            </tr>
          </thead>
          <tbody>
            {data.fields.map((f) => {
              const delta = f.before == null ? null : (f.after - f.before) * 100
              return (
                <tr key={f.field}>
                  <td><FieldName path={f.field} /></td>
                  <td className="num">{pct(f.before)}</td>
                  <td className="num">{pct(f.after)}</td>
                  <td className={`num${delta != null && delta < 0 ? ' neg' : ''}`}>{pts(delta)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

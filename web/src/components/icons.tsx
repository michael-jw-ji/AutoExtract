// 24x24 line glyphs drawn for this pipeline. Black strokes with one red detail;
// square caps to match the square-cornered UI.

import type { ReactNode } from 'react'

export type IconName =
  | 'xml' | 'fetch' | 'render' | 'read' | 'post' | 'validate' | 'grade'
  | 'failures' | 'change' | 'score' | 'gate' | 'ship' | 'reject'

const glyphs: Record<IconName, ReactNode> = {
  // A page with an XML tag on it: the answer key.
  xml: (
    <>
      <path d="M5 2.5h10l4 4v15H5z" />
      <path className="acc" d="M10 11l-2.5 2.5L10 16M14 11l2.5 2.5L14 16" />
    </>
  ),
  // The filing: a page with a folded corner and a 4 on it.
  fetch: (
    <>
      <path d="M5 2.5h10l4 4v15H5z M15 2.5v4h4" />
      <text className="ico-t" x="8.2" y="18">4</text>
    </>
  ),
  // Page to text: rows, each with a red label tag in front.
  render: (
    <>
      <path d="M9 6h11M9 12h11M9 18h8" />
      <path className="acc" d="M4 5h2.5v2H4zM4 11h2.5v2H4zM4 17h2.5v2H4z" />
    </>
  ),
  // The model: a chip with pins, 7B on it.
  read: (
    <>
      <path d="M5 5h14v14H5z" />
      <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
      <text className="ico-t sm" x="7.4" y="14.6">7B</text>
    </>
  ),
  // Derived in code: shares x price.
  post: (
    <>
      <path d="M2.5 9h5v6h-5zM16.5 9h5v6h-5z" />
      <path className="acc" d="M10 10l4 4M14 10l-4 4" />
    </>
  ),
  // Schema braces with a tick inside.
  validate: (
    <>
      <path d="M7 3H5.5v7L3 12l2.5 2v7H7M17 3h1.5v7L21 12l-2.5 2v7H17" />
      <path className="acc" d="M8.5 12.5l2.5 2.5 4.5-5" />
    </>
  ),
  // Field by field: two rows right, one wrong.
  grade: (
    <>
      <path d="M3 5.5l2 2 3-3.5M3 11.5l2 2 3-3.5M11 6h10M11 12h10M11 18h10" />
      <path className="acc" d="M3.5 16l4 4M7.5 16l-4 4" />
    </>
  ),
  // A table with one bad row.
  failures: (
    <>
      <path d="M3 4h18v16H3zM3 9.3h18M3 14.6h18" />
      <path className="acc" d="M5 12h14" strokeDasharray="2 2" />
    </>
  ),
  // The stack of layers, one of them changed.
  change: (
    <>
      <path d="M4 4h16v4H4zM4 16h16v4H4z" />
      <path className="acc" d="M4 10h16v4H4z" />
    </>
  ),
  // The holdout: a fixed grid of filings.
  score: (
    <>
      <path d="M4 5h2v2H4zM11 5h2v2h-2zM18 5h2v2h-2zM4 11h2v2H4zM18 11h2v2h-2zM4 17h2v2H4zM11 17h2v2h-2zM18 17h2v2h-2z" />
      <path className="accf" d="M11 11h2v2h-2z" />
    </>
  ),
  // The gate: a 95% interval sitting above the zero line.
  gate: (
    <>
      <path d="M2 18h20" strokeDasharray="2 2" />
      <path className="acc" d="M6 10h12M6 7.5v5M18 7.5v5" />
      <path className="accf" d="M10.5 8.5h3v3h-3z" />
    </>
  ),
  // Promoted: an arrow into the serving slot.
  ship: (
    <>
      <path d="M4 3h16v6H4z" />
      <path className="acc" d="M12 22V12M8.5 15.5L12 12l3.5 3.5" />
    </>
  ),
  // Rejected: the interval crosses zero.
  reject: (
    <>
      <path d="M2 12h20" strokeDasharray="2 2" />
      <path d="M6 12h12M6 9.5v5M18 9.5v5" />
      <path className="accf" d="M13.5 10.5h3v3h-3z" />
    </>
  ),
}

export function Icon({ name }: { name: IconName }) {
  return <g className="ico">{glyphs[name]}</g>
}

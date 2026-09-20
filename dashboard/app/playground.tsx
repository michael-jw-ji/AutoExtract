"use client";

/** Live extraction playground.
 *
 * The most interactive thing the dashboard can offer: paste any invoice-ish
 * text, watch it go through the real serving model and the real deterministic
 * validator, and see the failure signature it produces. persist=false so
 * playing with it never pollutes the corpus or the buffer.
 */

import { useState } from "react";
import { VIZ } from "./charts";
import { EXAMPLES } from "./examples";

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

type Result = {
  valid: boolean;
  data: Record<string, unknown> | null;
  errors: { type: string; loc: (string | number)[]; msg: string }[];
  signature: string | null;
  model: string;
  latency_ms: number;
  before?: {
    valid: boolean;
    errors: number;
    signature: string | null;
    error_list?: { type: string; loc: (string | number)[]; msg: string }[];
  };
  changed?: {
    field: string;
    label?: string;
    model: unknown;
    derived: unknown;
  }[];
  enrichment?: Record<string, unknown>;
  enrich_enabled?: boolean;
};

const SAMPLE = `INVOICE  NW-10024
From: Northwind Logistics Ltd, 14 Harbour Road, Bristol BS1 5TY, UK
To:   Pinegrove Retail Co, 51 Market Street, Leeds LS1 6DT, UK
Issued 01/03/2026        Currency: pounds

  Hex bolt M8x40 (box of 100)     10 x 12.50  =  125.00
  Packing tape 48mm                4 x  3.25  =   13.00

  Subtotal  138.00
  Tax        27.60
  TOTAL     165.60`;

/** Same-value test as the backend's: 24435.03 and "24435.03" are equal. */
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return false;
  const na = Number(a), nb = Number(b);
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na === nb;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

function diffAgainstGold(
  data: Record<string, unknown> | null,
  gold: Record<string, unknown> | null
): { field: string; got: unknown; want: unknown }[] {
  if (!data || !gold) return [];
  const out: { field: string; got: unknown; want: unknown }[] = [];
  for (const key of Object.keys(gold)) {
    const g = gold[key], d = data[key];
    if (g === null || typeof g === "object") continue;
    if (!same(d, g)) out.push({ field: key, got: d, want: g });
  }
  // Line-item categories, matched on description so a reordered list still
  // lines up. Everything else inside line_items is read off the page.
  const gi = (gold.line_items as Record<string, unknown>[]) || [];
  const di = (data.line_items as Record<string, unknown>[]) || [];
  const byDesc = new Map(
    di.map((i) => [String(i?.description ?? "").trim().toLowerCase(), i])
  );
  for (const item of gi) {
    const desc = String(item?.description ?? "").trim();
    const match = byDesc.get(desc.toLowerCase());
    if (match && !same(match.category, item.category)) {
      out.push({
        field: `${desc.slice(0, 20)} category`,
        got: match.category,
        want: item.category,
      });
    }
  }
  return out;
}

export function Playground() {
  const [text, setText] = useState(SAMPLE);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);

  // Loading an example clears the previous result: leaving a stale verdict on
  // screen next to freshly-loaded text is the kind of thing that gets noticed
  // from the audience at exactly the wrong moment.
  const load = (id: string) => {
    const ex = EXAMPLES.find((e) => e.id === id);
    if (!ex) return;
    setText(ex.text);
    setPicked(id);
    setResult(null);
    setErr(null);
  };

  const current = EXAMPLES.find((e) => e.id === picked);

  // Fields where the final answer disagrees with ground truth. Only possible
  // for the built-in examples, which ship their gold.
  //
  // This is the beat that matters most: enrichment makes output VALID, and a
  // valid answer that still disagrees with gold is exactly the thing that
  // poisons a training set. Without this the browser can only say "it worked".
  const wrong = diffAgainstGold(result?.data ?? null, current?.gold ?? null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await fetch(`${API}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, persist: false }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setResult((await r.json()) as Result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pg">
      <div className="pg-left">
        <div className="filters">
          <span className="flabel">load a document</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.id}
              className={`chip${picked === ex.id ? " on" : ""}`}
              onClick={() => load(ex.id)}
              title={ex.caption}
            >
              {ex.label}
            </button>
          ))}
          <button
            className={`chip${picked === null ? " on" : ""}`}
            onClick={() => { setText(SAMPLE); setPicked(null); setResult(null); }}
          >
            short sample
          </button>
        </div>
        {current && <div className="pg-hint">{current.caption}</div>}
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setPicked(null); }}
          spellCheck={false}
          placeholder="paste a document…"
        />
        <div className="pg-actions">
          <button className="btn" onClick={run} disabled={busy || !text.trim()}>
            {busy ? "extracting…" : "▶ extract"}
          </button>
          <span className="dim">
            not persisted — safe to experiment
          </span>
        </div>
      </div>

      <div className="pg-right">
        {busy && <div className="empty">calling the serving model…</div>}
        {err && <div className="err">error: {err}</div>}
        {!busy && !err && !result && (
          <div className="empty">
            run an extraction to see validation output
          </div>
        )}
        {result && (
          <>
            <div className="pg-verdict">
              <span
                className={`badge ${result.valid ? "promoted" : "rejected"}`}
              >
                {result.valid ? "VALID" : "INVALID"}
              </span>
              <span className="dim">{result.model}</span>
              <span className="dim">{result.latency_ms} ms</span>
              <span className="dim">{result.errors.length} error(s)</span>
            </div>

            {result.enrich_enabled && result.before && (
              <div className="pg-delta">
                <div className="pg-sig-k">model answer → after enrichment</div>
                <div className="pg-delta-row">
                  <span className={`badge ${result.before.valid ? "promoted" : "rejected"}`}>
                    {result.before.valid ? "VALID" : `${result.before.errors} errors`}
                  </span>
                  <span className="arrow">→</span>
                  <span className={`badge ${result.valid ? "promoted" : "rejected"}`}>
                    {result.valid ? "VALID" : `${result.errors.length} errors`}
                  </span>
                  <span className="dim">
                    {result.before.valid === false && result.valid
                      ? "fixed by code, no model call"
                      : ""}
                  </span>
                </div>
              </div>
            )}

            {current?.gold && (
              <div className={`pg-truth${wrong.length ? " pg-truth-bad" : ""}`}>
                {wrong.length === 0 ? (
                  <>
                    <b>Matches ground truth</b> on every scored field.
                  </>
                ) : (
                  <>
                    <div className="pg-truth-head">
                      {result.valid ? (
                        <>
                          <b>Valid — and still wrong.</b> It passed every format,
                          arithmetic and business rule, and {wrong.length} field
                          {wrong.length === 1 ? "" : "s"} still disagree with
                          ground truth.
                        </>
                      ) : (
                        <>
                          <b>{wrong.length} field
                          {wrong.length === 1 ? "" : "s"} disagree with ground
                          truth.</b>
                        </>
                      )}
                    </div>
                    {wrong.slice(0, 6).map((wf, i) => (
                      <div className="pg-changed-row" key={i}>
                        <span className="pg-changed-f">{wf.field}</span>
                        <span className="pg-was">{String(wf.got ?? "—")}</span>
                        <span className="arrow">want</span>
                        <span className="pg-now">{String(wf.want ?? "—")}</span>
                      </div>
                    ))}
                    {result.valid && (
                      <div className="pg-truth-note">
                        This is why a repair is checked against ground truth
                        before it is allowed to train anything — a fix that
                        merely looks valid can still be wrong.
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* The money shot. With enrichment on, the final answer is valid
                and the error list is empty -- so without this the audience
                sees "it worked" and never sees what the model got wrong. */}
            {result.changed && result.changed.length > 0 && (
              <div className="pg-changed">
                <div className="pg-sig-k">
                  what the model invented → what code derived
                </div>
                {result.changed.map((c, i) => (
                  <div className="pg-changed-row" key={i}>
                    <span className="pg-changed-f">
                      {c.label || c.field}
                    </span>
                    <span className="pg-was">{String(c.model ?? "—")}</span>
                    <span className="arrow">→</span>
                    <span className="pg-now">{String(c.derived ?? "—")}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Pre-enrichment errors. Same reason: these are the only place
                the registry/taxonomy violations are stated in English. */}
            {result.valid && result.before && !result.before.valid &&
             result.before.error_list && result.before.error_list.length > 0 && (
              <div className="pg-errs">
                {result.before.error_list.slice(0, 6).map((e, i) => (
                  <div className="pg-err" key={i}>
                    <span
                      className="pg-err-type"
                      style={{
                        color: e.type.startsWith("registry")
                          ? VIZ.s2
                          : e.type.startsWith("taxonomy") ||
                            e.type.startsWith("policy")
                          ? VIZ.warning
                          : VIZ.muted,
                      }}
                    >
                      {e.type}
                    </span>
                    <span className="dim">@ {e.loc.join(".") || "<root>"}</span>
                    <div className="pg-err-msg">{e.msg}</div>
                  </div>
                ))}
              </div>
            )}

            {result.enrichment && Object.keys(result.enrichment).length > 0 && (
              <div className="pg-enrich">
                <div className="pg-sig-k">how each field was resolved</div>
                {Object.entries(result.enrichment).map(([k, v]) => (
                  <div className="pg-enrich-row" key={k}>
                    <span>{k}</span>
                    <b>{Array.isArray(v) ? v.join(", ") : String(v)}</b>
                  </div>
                ))}
              </div>
            )}

            {result.signature && (
              <div className="pg-sig">
                <div className="pg-sig-k">failure signature</div>
                <code>{result.signature}</code>
              </div>
            )}

            {result.errors.length > 0 && (
              <div className="pg-errs">
                {result.errors.slice(0, 8).map((e, i) => (
                  <div className="pg-err" key={i}>
                    <span
                      className="pg-err-type"
                      style={{
                        color: e.type.startsWith("registry")
                          ? VIZ.s2
                          : e.type.startsWith("taxonomy") ||
                            e.type.startsWith("policy")
                          ? VIZ.warning
                          : VIZ.muted,
                      }}
                    >
                      {e.type}
                    </span>
                    <span className="dim">
                      @ {e.loc.join(".") || "<root>"}
                    </span>
                    <div className="pg-err-msg">{e.msg}</div>
                  </div>
                ))}
              </div>
            )}

            {result.data && (
              <>
                <button
                  className="linkbtn"
                  onClick={() => setShowJson((s) => !s)}
                >
                  {showJson ? "hide" : "show"} extracted JSON
                </button>
                {showJson && (
                  <pre className="pg-json">
                    {JSON.stringify(result.data, null, 2)}
                  </pre>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

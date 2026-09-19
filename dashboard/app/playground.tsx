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

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

type Result = {
  valid: boolean;
  data: Record<string, unknown> | null;
  errors: { type: string; loc: (string | number)[]; msg: string }[];
  signature: string | null;
  model: string;
  latency_ms: number;
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

export function Playground() {
  const [text, setText] = useState(SAMPLE);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);

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
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          placeholder="paste invoice text…"
        />
        <div className="pg-actions">
          <button className="btn" onClick={run} disabled={busy || !text.trim()}>
            {busy ? "extracting…" : "▶ extract"}
          </button>
          <button className="linkbtn" onClick={() => setText(SAMPLE)}>
            reset sample
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

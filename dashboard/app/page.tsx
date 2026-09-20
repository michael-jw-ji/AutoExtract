"use client";

import { useCallback, useEffect, useState } from "react";
import { Funnel, HBar, TableView, TrendLine, VIZ, type BarDatum } from "./charts";
import { Playground } from "./playground";

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

type Stats = {
  incumbent: string | null;
  extractions: number;
  passed: number;
  failed: number;
  valid_rate: number;
  avg_latency_ms: number;
  buffer_depth: number;
  model_versions: number;
  promotions: Record<string, number>;
};
type Version = {
  id: number; name: string; status: string; track: string | null;
  field_f1: number | null; valid_rate: number | null;
  decision: string | null; margin: number | null; reason: string | null;
  dataset_size: number | null;
};
type Cluster = { signature: string; count: number; labels: string[] };
type Tick = { id: number; valid: number; latency_ms: number | null };
type Field = { field: string; accuracy: number; kind: string };
type RepairStats = {
  by_method: { method: string; verified: number; total: number }[];
  by_status: Record<string, number>;
  failures: number; repairs: number; verified: number;
};
type Latency = { buckets: { label: string; count: number }[]; p50: number; p95: number; n: number };
type Config = {
  domain: string; available_domains: string[]; schema: string;
  json_mode: boolean; enrich: boolean; serving_model: string; db: string;
};
type Failure = {
  id: number; signature: string; error_count: number; status: string;
  doc_excerpt: string; messiness: string;
};

async function get<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API}${path}`, { cache: "no-store" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export default function Page() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [timeline, setTimeline] = useState<Tick[]>([]);
  const [fields, setFields] = useState<Field[]>([]);
  const [repairs, setRepairs] = useState<RepairStats | null>(null);
  const [latency, setLatency] = useState<Latency | null>(null);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [offline, setOffline] = useState(false);
  const [paused, setPaused] = useState(false);

  const [selectedSig, setSelectedSig] = useState<string | null>(null);
  const [drill, setDrill] = useState<Failure[]>([]);
  const [tables, setTables] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setTables((t) => ({ ...t, [k]: !t[k] }));

  // filters
  const [clusterStatus, setClusterStatus] = useState<string>("all");
  const [clusterLimit, setClusterLimit] = useState<number>(12);
  const [fieldKind, setFieldKind] = useState<string>("all");
  const [sortKey, setSortKey] = useState<keyof Version>("id");
  const [sortDesc, setSortDesc] = useState(false);

  const load = useCallback(async () => {
    const [s, v, c, t, f, r, l, k] = await Promise.all([
      get<Stats>("/api/stats"),
      get<Version[]>("/api/versions"),
      get<Cluster[]>(
        `/api/clusters?limit=${clusterLimit}` +
          (clusterStatus === "all" ? "" : `&status=${clusterStatus}`)
      ),
      get<Tick[]>("/api/timeline?limit=300"),
      get<Field[]>("/api/fields"),
      get<RepairStats>("/api/repair-stats"),
      get<Latency>("/api/latency"),
      get<Config>("/api/config"),
    ]);
    setOffline(s === null);
    if (s) setStats(s);
    if (v) setVersions(v);
    if (c) setClusters(c);
    if (t) setTimeline(t.slice().reverse());
    if (f) setFields(f);
    if (r) setRepairs(r);
    if (l) setLatency(l);
    if (k) setCfg(k);
  }, [clusterLimit, clusterStatus]);

  useEffect(() => {
    load();
    if (paused) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load, paused]);

  useEffect(() => {
    if (!selectedSig) return void setDrill([]);
    get<Failure[]>(`/api/failures?limit=8&signature=${encodeURIComponent(selectedSig)}`)
      .then((d) => setDrill(d ?? []));
  }, [selectedSig]);

  const promoted = stats?.promotions?.promoted ?? 0;
  const rejected = stats?.promotions?.rejected ?? 0;

  // Rolling valid-rate over a 20-extraction window: a raw pass/fail series is
  // all-or-nothing per point, which reads as noise rather than trend.
  const WINDOW = 20;
  const trend = timeline
    .map((_, i) => {
      if (i < WINDOW - 1) return null;
      const slice = timeline.slice(i - WINDOW + 1, i + 1);
      return {
        i,
        value: slice.filter((x) => x.valid).length / WINDOW,
        label: `extractions ${i - WINDOW + 2}–${i + 1}`,
      };
    })
    .filter(Boolean) as { i: number; value: number; label: string }[];

  const clusterBars: BarDatum[] = clusters.map((c) => ({
    key: c.signature,
    label: c.labels.join("  +  "),
    value: c.count,
    sub: c.signature.slice(0, 90),
  }));

  const fieldBars: BarDatum[] = fields
    .filter((f) => fieldKind === "all" || f.kind === fieldKind)
    .map((f) => ({
      key: f.field,
      label: f.field,
      value: f.accuracy,
      sub:
        f.kind === "business_rule"
          ? "business rule — not in the document"
          : "read from the document",
      accent: f.kind === "business_rule" ? VIZ.s2 : VIZ.s1,
    }));

  const sortedVersions = [...versions].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const an = av === null || av === undefined ? -Infinity : av;
    const bn = bv === null || bv === undefined ? -Infinity : bv;
    const cmp = typeof an === "number" && typeof bn === "number"
      ? an - bn
      : String(an).localeCompare(String(bn));
    return sortDesc ? -cmp : cmp;
  });

  const sortBy = (k: keyof Version) => {
    if (k === sortKey) setSortDesc((d) => !d);
    else { setSortKey(k); setSortDesc(false); }
  };
  const arrow = (k: keyof Version) => (k === sortKey ? (sortDesc ? " ▾" : " ▴") : "");

  const mech = repairs?.by_method.find((m) => m.method === "mechanical");
  const dist = repairs?.by_method.find((m) => m.method === "distill");
  const unrepairable = repairs ? repairs.failures - repairs.repairs : 0;

  return (
    <div className="wrap">
      <header>
        <h1>AUTOEXTRACT</h1>
        <span className="sub">serve → verify → buffer → cluster → repair → train → gate</span>
        <button className="linkbtn pause" onClick={() => setPaused((p) => !p)}>
          {paused ? "▶ resume" : "❚❚ pause"}
        </button>
        <span className="live" style={{ color: offline ? VIZ.critical : undefined }}>
          {offline ? "● api unreachable" : paused ? "● paused" : "● live"}
        </span>
      </header>

      {cfg && (
        <div className="cfgbar">
          <span className="flabel">domain</span>
          <span className={`track track-${cfg.domain === "invoice" ? "baseten" : "local"}`}>
            {cfg.domain}
          </span>
          <span className="dim">{cfg.schema}</span>
          <span className="flabel">pipeline</span>
          <span className={`chip${cfg.json_mode ? " on" : ""}`}>
            json mode {cfg.json_mode ? "on" : "off"}
          </span>
          <span className={`chip${cfg.enrich ? " on" : ""}`}>
            enrichment {cfg.enrich ? "on" : "off"}
          </span>
          <span className="cfg-right dim">
            {cfg.serving_model} · {cfg.db}
          </span>
        </div>
      )}

      <div className="stats">
        <Stat k="incumbent" v={stats?.incumbent ?? "—"} />
        <Stat k="extractions" v={stats?.extractions ?? 0} />
        <Stat k="valid rate" v={stats ? `${(stats.valid_rate * 100).toFixed(1)}%` : "—"} />
        <Stat k="buffer depth" v={stats?.buffer_depth ?? 0} />
        <Stat k="versions" v={stats?.model_versions ?? 0} />
        <Stat
          k="promoted / rejected"
          v={`${promoted} / ${rejected}`}
          warn={rejected === 0 ? "gate has never rejected" : undefined}
        />
      </div>

      <Panel
        title="live extraction playground"
        note="paste any invoice text — real model, real validator, nothing persisted"
      >
        <Playground />
      </Panel>

      <div className="grid2">
        <Panel
          title="valid rate — rolling 20-extraction window"
          note="rolling, not per-document: a pass/fail series reads as noise rather than trend"
        >
          <TrendLine points={trend} />
        </Panel>

        <Panel
          title="repair funnel"
          note="only VERIFIED repairs reach a training set"
        >
          {repairs && repairs.repairs === 0 ? (
            <div className="empty">
              {repairs.failures === 0
                ? "no failures to repair"
                : `${repairs.failures} failure${repairs.failures === 1 ? "" : "s"} buffered — no repair pass has run yet`}
            </div>
          ) : repairs && (
            <>
              <Funnel
                stages={[
                  { label: "mechanical ✓", value: mech?.verified ?? 0, color: VIZ.s1 },
                  { label: "distilled ✓", value: dist?.verified ?? 0, color: VIZ.s3 },
                  {
                    label: "unverified",
                    value: repairs.repairs - repairs.verified,
                    color: VIZ.s2,
                  },
                  { label: "unrepairable", value: Math.max(0, unrepairable), color: VIZ.muted },
                ]}
              />
              <div className="funnel-summary">
                {repairs.failures} failures → {repairs.repairs} repaired →{" "}
                <b>{repairs.verified} verified</b>{" "}
                ({repairs.failures ? ((repairs.verified / repairs.failures) * 100).toFixed(0) : 0}% yield)
              </div>
            </>
          )}
        </Panel>
      </div>

      <Panel
        title="per-field accuracy — latest eval"
        note="orange = business rules the model cannot read off the document"
      >
        <div className="filters">
          <span className="flabel">show</span>
          {[
            ["all", "all fields"],
            ["business_rule", "business rules"],
            ["extraction", "read from document"],
          ].map(([v, label]) => (
            <button
              key={v}
              className={`chip${fieldKind === v ? " on" : ""}`}
              onClick={() => setFieldKind(v)}
            >
              {label}
            </button>
          ))}
        </div>
        {fieldBars.length ? (
          <>
            <HBar
              data={fieldBars}
              domainMax={1}
              format={(v) => `${(v * 100).toFixed(0)}%`}
            />
            <TableView
              open={!!tables.fields}
              onToggle={() => toggle("fields")}
              headers={["field", "accuracy", "kind"]}
              rows={fields.map((f) => [f.field, `${(f.accuracy * 100).toFixed(1)}%`, f.kind])}
            />
          </>
        ) : (
          <div className="empty">no eval scored yet</div>
        )}
      </Panel>

      <Panel
        title="model lineage & gate decisions"
        note="tracks are gated separately — a local LoRA is never compared against the Baseten incumbent"
      >
        {versions.length === 0 ? (
          <div className="empty">no model versions yet</div>
        ) : (
          <table>
            <thead>
              <tr>
                {([
                  ["id", "#"], ["track", "track"], ["name", "version"],
                  ["dataset_size", "dataset"],
                  ["valid_rate", "valid"], ["field_f1", "score"],
                  ["margin", "margin"], ["decision", "decision"],
                ] as [keyof Version, string][]).map(([k, label]) => (
                  <th key={k} className="sortable" onClick={() => sortBy(k)}>
                    {label}{arrow(k)}
                  </th>
                ))}
                <th>reason</th>
              </tr>
            </thead>
            <tbody>
              {sortedVersions.map((v) => (
                <tr key={v.id}>
                  <td className="dim">{v.id}</td>
                  <td>
                    <span className={`track track-${v.track ?? "baseten"}`}>
                      {v.track ?? "baseten"}
                    </span>
                  </td>
                  <td>{v.name}</td>
                  <td className="dim">{v.dataset_size ?? "—"}</td>
                  <td>{v.valid_rate != null ? `${(v.valid_rate * 100).toFixed(1)}%` : "—"}</td>
                  <td>{v.field_f1 != null ? v.field_f1.toFixed(4) : "—"}</td>
                  <td className={v.margin == null ? "" : `delta ${v.margin >= 0 ? "up" : "down"}`}>
                    {v.margin != null ? `${v.margin >= 0 ? "+" : ""}${v.margin.toFixed(2)}pp` : "—"}
                  </td>
                  <td><span className={`badge ${v.decision ?? "candidate"}`}>{v.decision ?? "candidate"}</span></td>
                  <td className="reason">{v.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel
        title="failure clusters — deterministic signatures, no embeddings"
        note="click a bar to inspect the documents behind it"
      >
        <div className="filters">
          <span className="flabel">status</span>
          {["all", "new", "repaired", "unrepairable"].map((s) => (
            <button
              key={s}
              className={`chip${clusterStatus === s ? " on" : ""}`}
              onClick={() => { setClusterStatus(s); setSelectedSig(null); }}
            >
              {s}
            </button>
          ))}
          <span className="flabel">top</span>
          {[6, 12, 25].map((n) => (
            <button
              key={n}
              className={`chip${clusterLimit === n ? " on" : ""}`}
              onClick={() => setClusterLimit(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <HBar
          data={clusterBars}
          unit="×"
          onSelect={(k) => setSelectedSig((s) => (s === k ? null : k))}
          selected={selectedSig}
        />
        <TableView
          open={!!tables.clusters}
          onToggle={() => toggle("clusters")}
          headers={["count", "signature"]}
          rows={clusters.map((c) => [c.count, c.signature])}
        />
        {selectedSig && (
          <div className="drill">
            <div className="drill-head">
              {drill.length} example{drill.length === 1 ? "" : "s"} ·{" "}
              <code>{selectedSig.slice(0, 80)}</code>
              <button className="linkbtn" onClick={() => setSelectedSig(null)}>close</button>
            </div>
            {drill.map((f) => (
              <div className="drill-row" key={f.id}>
                <div className="drill-meta">
                  #{f.id} · {f.messiness} · {f.error_count} errors · {f.status}
                </div>
                <pre>{f.doc_excerpt.slice(0, 260)}…</pre>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="extraction latency" note="the slow tail is what bites during a live demo">
        {latency && latency.n > 0 ? (
          <>
            <div className="latrow">
              <span>p50 <b>{(latency.p50 / 1000).toFixed(1)}s</b></span>
              <span>p95 <b>{(latency.p95 / 1000).toFixed(1)}s</b></span>
              <span>n <b>{latency.n}</b></span>
            </div>
            <HBar
              data={latency.buckets.map((b) => ({ key: b.label, label: b.label, value: b.count }))}
            />
          </>
        ) : (
          <div className="empty">no latency data</div>
        )}
      </Panel>
    </div>
  );
}

function Panel({
  title, note, children,
}: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="panel">
      <h2>
        {title}
        {note && <span className="note">{note}</span>}
      </h2>
      <div className="body">{children}</div>
    </div>
  );
}

function Stat({ k, v, warn }: { k: string; v: string | number; warn?: string }) {
  return (
    <div className={`stat${warn ? " stat-warn" : ""}`} title={warn}>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {warn && <div className="warn">⚠ {warn}</div>}
    </div>
  );
}

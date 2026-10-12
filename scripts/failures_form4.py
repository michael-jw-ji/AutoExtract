"""Read the model's failures by hand, and record what you conclude.

    python scripts/failures_form4.py                 # then open http://127.0.0.1:8766
    python scripts/failures_form4.py --model <id>    # another model's recorded answers

Reads the RECORDED holdout answers (data/eval_cache/, written by the scorer),
so it costs nothing and needs no model. For each filing it shows the page text,
the validation errors, and every row of the answer next to gold: matching,
differing (field by field), extra (the model invented it) or missing.

The automatic theme on each filing is a first guess. Your notes are the real
output: one file per filing in data/form4/failure_notes/<answers>/ (tracked),
recording the theme, the layer the fix belongs in, and the root cause. Those
notes are what FAILURES.md is written from.

Binds to localhost only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from functools import lru_cache
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from core.config import settings
from evalgate import scorer
from evalgate.failures import analyze

ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
FIX_LAYERS = ["text rendering", "enrichment (code)", "prompt", "model capability",
              "gold / eval set", "not a model error (ambiguous)"]

app = FastAPI()
STATE: dict = {}


def _answers_path() -> Path:
    return STATE["answers"]


def _notes_dir() -> Path:
    return settings.data_dir / "form4" / "failure_notes" / _answers_path().stem


@lru_cache(maxsize=1)
def _load() -> dict[str, dict]:
    con = sqlite3.connect(settings.db_path)
    docs = {r[0]: r for r in con.execute(
        "SELECT id, ext_id, text, gold_json, ambiguous_json, messiness FROM documents "
        "WHERE split='holdout'")}
    out = {}
    for line in _answers_path().read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["doc_id"] not in docs:
            continue
        _, acc, text, gold, amb, tags = docs[rec["doc_id"]]
        g = json.loads(gold)
        out[acc] = {"accession": acc, "doc_id": rec["doc_id"], "text": text, "gold": g,
                    "tags": tags, "latency_ms": rec.get("latency_ms"),
                    "analysis": analyze(text, g, rec["raw"], json.loads(amb or "[]")),
                    "raw": rec["raw"], "company": g.get("issuer_name", "")}
    return out


def _note(acc: str) -> dict | None:
    p = _notes_dir() / f"{acc}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _review(acc: str) -> dict | None:
    """AI pre-review (scripts/prereview_failures.py). Advice; the note is the decision."""
    p = settings.data_dir / "form4" / "failure_review" / _answers_path().stem / f"{acc}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@app.get("/api/list")
def list_items() -> dict:
    items = []
    for acc, d in _load().items():
        a = d["analysis"]
        if a["themes"] == ["correct"]:
            continue
        n, r = _note(acc), _review(acc)
        items.append({"accession": acc, "company": d["company"], "valid": a["valid"],
                      "f1": a["f1"], "themes": [t["theme"] for t in r["themes"]] if r else a["themes"],
                      "review": r["verdict"] if r else None,
                      "noted": n["theme"] if n else None,
                      "human": bool(n and n.get("human_reviewed", True))})
    order = {"check": 0, "policy": 1, "confirm": 2, None: 3}
    items.sort(key=lambda i: (order[i["review"]], i["valid"], i["f1"], i["accession"]))
    auto = Counter(t for i in items for t in i["themes"])
    return {"items": items, "answers": _answers_path().name, "total": len(_load()),
            "auto_themes": auto.most_common(), "noted": sum(1 for i in items if i["noted"]),
            "verdicts": Counter(i["review"] for i in items if i["review"]),
            "open_check": sum(1 for i in items if i["review"] == "check" and not i["noted"]),
            "fix_layers": FIX_LAYERS,
            "known_themes": sorted({i["noted"] for i in items if i["noted"]} | set(auto))}


@app.get("/api/doc/{acc}")
def doc(acc: str) -> dict:
    if not ACC.match(acc) or acc not in _load():
        raise HTTPException(404, "unknown filing")
    d = dict(_load()[acc])
    d["note"] = _note(acc)
    d["review"] = _review(acc)
    return d


@app.post("/api/accept/confirm")
def accept_confirmed() -> dict:
    """Turn every 'confirm' pre-review without a note into a note, marked as NOT
    individually reviewed by a person. FAILURES.md reports the two kinds apart."""
    written = 0
    _notes_dir().mkdir(parents=True, exist_ok=True)
    for acc, d in _load().items():
        r = _review(acc)
        if not r or r["verdict"] != "confirm" or _note(acc):
            continue
        first = r["themes"][0]
        note = {"accession": acc, "theme": first["theme"], "fix_layer": first["fix_layer"],
                "root_cause": first["root_cause"],
                "other_themes": [t["theme"] for t in r["themes"][1:]],
                "auto_themes": d["analysis"]["themes"], "valid": d["analysis"]["valid"],
                "f1": d["analysis"]["f1"], "answers": _answers_path().name,
                "assisted_by": r["reviewer"], "human_reviewed": False}
        (_notes_dir() / f"{acc}.json").write_text(json.dumps(note, indent=2), encoding="utf-8")
        written += 1
    return {"accepted": written}


@app.post("/api/note/{acc}")
def save(acc: str, body: dict) -> dict:
    if not ACC.match(acc) or acc not in _load():
        raise HTTPException(404, "unknown filing")
    theme = str(body.get("theme", "")).strip()
    layer = str(body.get("fix_layer", "")).strip()
    if not theme or layer not in FIX_LAYERS:
        raise HTTPException(422, "pick a theme and a fix layer")
    d = _load()[acc]
    r = _review(acc)
    note = {"accession": acc, "theme": theme, "fix_layer": layer,
            "root_cause": str(body.get("root_cause", "")).strip(),
            "other_themes": [str(t) for t in body.get("other_themes", []) if str(t).strip()],
            "auto_themes": d["analysis"]["themes"], "valid": d["analysis"]["valid"],
            "f1": d["analysis"]["f1"], "answers": _answers_path().name,
            "assisted_by": r["reviewer"] if r else None, "human_reviewed": True}
    _notes_dir().mkdir(parents=True, exist_ok=True)
    (_notes_dir() / f"{acc}.json").write_text(json.dumps(note, indent=2), encoding="utf-8")
    return {"ok": True}


@app.get("/api/summary")
def summary() -> dict:
    notes = [json.loads(p.read_text(encoding="utf-8")) for p in _notes_dir().glob("*.json")]
    by = Counter((n["theme"], n["fix_layer"]) for n in notes)
    return {"notes": len(notes),
            "themes": [{"theme": t, "fix_layer": l, "count": c} for (t, l), c in by.most_common()]}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Form 4 failures</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--pane:#f6f6f4;--acc:#1f5fbf;--ok:#1a7f37;--bad:#c62828;--warn:#b35900;--okbg:#e9f6ec;--badbg:#fdecea;--warnbg:#fff3e0}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e6e6e6;--mut:#999;--line:#333;--pane:#1f1f1f;--acc:#6ea8fe;--ok:#4cc26b;--bad:#ef6b6b;--warn:#e0a050;--okbg:#16301d;--badbg:#3a1a1a;--warnbg:#3a2a12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header b{font-size:15px}.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{border:1px solid var(--line);border-radius:12px;padding:2px 9px;font-size:12px;cursor:pointer;background:none;color:var(--fg)}.chip.on{background:var(--acc);color:#fff;border-color:var(--acc)}
main{flex:1;display:grid;grid-template-columns:270px 1fr 1fr;min-height:0}
section{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
h2{margin:0;padding:7px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);border-bottom:1px solid var(--line)}
.scroll{overflow:auto;flex:1}
.item{padding:7px 12px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.on{background:var(--pane)}
.item small{color:var(--mut);display:block}.t{font-size:11px;color:var(--mut)}
.noted::before{content:"\\2713 ";color:var(--ok)}
pre{margin:0;padding:12px;font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-word;background:var(--pane);flex:1;overflow:auto}
mark{background:var(--warnbg);color:var(--fg)}
.diff{padding:10px 12px}.diff h3{font-size:13px;margin:12px 0 4px}
table{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:6px}td,th{border:1px solid var(--line);padding:3px 5px;text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:500}.ok{background:var(--okbg)}.bad{background:var(--badbg)}.amb{opacity:.55}
.kind{font-weight:600;font-size:11px;text-transform:uppercase}.k-extra{color:var(--bad)}.k-missing{color:var(--warn)}.k-diff{color:var(--warn)}.k-match{color:var(--ok)}
.err{font:12px ui-monospace,Consolas,monospace;color:var(--bad)}
footer{padding:9px 16px;border-top:1px solid var(--line);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input,select{padding:6px 8px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
button{padding:7px 14px;border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:4px;cursor:pointer}
#msg{color:var(--warn)}details{font-size:12px;margin-top:8px}a{color:var(--acc)}
#rev{border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin-bottom:10px;background:var(--pane)}
#rev ul{margin:4px 0;padding-left:18px}#rev li{margin:2px 0}
.v{font-size:11px;font-weight:600;border-radius:8px;padding:0 6px;margin-left:4px}
.v-check{color:#fff;background:var(--bad)}.v-policy{color:#fff;background:var(--warn)}.v-confirm{color:var(--ok);border:1px solid var(--ok)}
button.sec{background:none;color:var(--acc);padding:4px 10px;font-size:12px}
@media (max-width:1000px){main{grid-template-columns:1fr}}
</style></head><body>
<header><b>Form 4 failures</b><span id="prog"></span><span class="t" id="ans"></span><div class="chips" id="chips"></div></header>
<main>
<section><h2>Filings, worst first</h2><div class="scroll" id="list"></div></section>
<section><h2>Page text (rows from the diff highlighted)</h2><pre id="text"></pre></section>
<section><h2>Answer vs gold</h2><div class="scroll diff" id="diff"></div></section>
</main>
<footer>
<input id="theme" list="themes" placeholder="theme, e.g. placeholder row for an empty table" style="min-width:300px"><datalist id="themes"></datalist>
<select id="layer"></select>
<input id="cause" placeholder="root cause, one sentence" style="flex:1;min-width:240px">
<button id="save">Save &amp; next</button>
<button class="sec" id="acceptall" title="Writes a note for every AI 'confirm' suggestion you have not noted, marked as not individually reviewed">Accept all AI-confirmed</button>
<span id="msg"></span>
</footer>
<script>
let L=null,cur=null,filter=null,sug=null,others=[];const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const fmt=v=>v===null||v===undefined?'<span class="t">null</span>':esc(typeof v==='object'?JSON.stringify(v):v);
async function load(){L=await (await fetch('/api/list')).json();
 $('prog').textContent=L.noted+' noted / '+L.items.length+' with problems ('+L.total+' scored) · '+L.open_check+' still need you';$('ans').textContent=L.answers;
 $('themes').innerHTML=L.known_themes.map(t=>'<option value="'+esc(t)+'">').join('');
 $('layer').innerHTML='<option value="">fix belongs in…</option>'+L.fix_layers.map(x=>'<option>'+esc(x)+'</option>').join('');
 const special=[['@check','needs you',L.verdicts.check||0],['@policy','policy question',L.verdicts.policy||0],['@confirm','AI: confirm',L.verdicts.confirm||0]];
 $('chips').innerHTML='<button class="chip'+(filter?'':' on')+'" data-t="">all</button>'+special.map(([k,lab,n])=>'<button class="chip'+(filter===k?' on':'')+'" data-t="'+k+'"><b>'+lab+'</b> '+n+'</button>').join('')+L.auto_themes.map(([t,n])=>'<button class="chip'+(filter===t?' on':'')+'" data-t="'+esc(t)+'">'+esc(t)+' '+n+'</button>').join('');
 document.querySelectorAll('.chip').forEach(b=>b.onclick=()=>{filter=b.dataset.t||null;load()});
 const items=L.items.filter(i=>!filter||(filter[0]==='@'?i.review===filter.slice(1):i.themes.includes(filter)));
 $('list').innerHTML='';items.forEach(i=>{const d=document.createElement('div');d.className='item'+(i.accession===cur?' on':'');
  d.innerHTML='<span class="'+(i.noted?'noted':'')+'">'+esc(i.company)+'</span>'+(i.review?'<span class="v v-'+i.review+'">'+(i.review==='check'?'needs you':i.review)+'</span>':'')+'<small>'+i.accession+' &middot; '+(i.valid?'valid':'INVALID')+' &middot; F1 '+i.f1.toFixed(3)+'</small><span class="t">'+esc(i.themes.join(', '))+'</span>';
  d.onclick=()=>open_(i.accession);$('list').appendChild(d)});return items}
function groupErr(errs){const g=new Map();errs.forEach(e=>{const k=e.type+' @ '+e.loc.replace(/\\.\\d+\\./g,'.*.');const v=g.get(k)||{n:0,msg:e.msg};v.n++;g.set(k,v)});
 return [...g].map(([k,v])=>'<div class="err">'+(v.n>1?v.n+'&times; ':'')+esc(k)+' &mdash; '+esc(v.msg)+'</div>').join('')}
function rowTable(rows){if(!rows.length)return '<div class="t">none</div>';
 return rows.map(r=>'<div class="kind k-'+r.kind+'">'+r.kind+'</div><table><tr><th>field</th><th>answer</th><th>gold</th></tr>'+
  r.cells.map(c=>'<tr class="'+(c.ambiguous?'amb':c.ok?'ok':'bad')+'"><td>'+esc(c.field)+(c.ambiguous?' (ambiguous)':'')+'</td><td>'+fmt(c.pred)+'</td><td>'+fmt(c.gold)+'</td></tr>').join('')+'</table>').join('')}
async function open_(acc){cur=acc;const d=await (await fetch('/api/doc/'+acc)).json(),a=d.analysis;
 const hl=new Set();a.tables.transactions.filter(r=>r.kind!=='match').forEach(r=>r.cells.forEach(c=>{if(['shares','shares_owned_after'].includes(c.field))[c.pred,c.gold].forEach(v=>{const n=Number(String(v).replace(/,/g,''));if(n>0)hl.add(n.toLocaleString('en-US',{maximumFractionDigits:6}))})}));
 $('text').innerHTML=d.text.split('\\n').map(l=>{const e=esc(l);return [...hl].some(h=>l.includes(h))?'<mark>'+e+'</mark>':e}).join('\\n');
 const r=d.review;sug=r;
 const revHtml=r?'<div id="rev"><b>AI pre-review</b><span class="v v-'+r.verdict+'">'+(r.verdict==='check'?'needs your judgement':r.verdict==='policy'?'one policy decision':'confirm')+'</span>'+
  (r.why_check.length?'<div style="margin-top:4px"><b>Why you:</b><ul>'+r.why_check.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul></div>':'')+
  '<ul>'+r.themes.map(t=>'<li><b>'+esc(t.theme)+'</b> <span class="t">['+esc(t.fix_layer)+']</span> &mdash; '+esc(t.root_cause)+'</li>').join('')+'</ul>'+
  '<button class="sec" id="usesug">Use suggestion</button> <span class="t">fills the form; you still save</span></div>':'';
 $('diff').innerHTML=revHtml+'<div>'+(a.valid?'<b style="color:var(--ok)">valid</b>':'<b style="color:var(--bad)">invalid</b>')+' &middot; field F1 '+a.f1.toFixed(3)+' &middot; '+(d.latency_ms/1000).toFixed(1)+'s &middot; <span class="t">auto: '+esc(a.themes.join(', '))+'</span></div>'+
  (a.errors.length?'<h3>Validation errors</h3>'+groupErr(a.errors):'')+
  '<h3>Top-level fields</h3><table><tr><th>field</th><th>answer</th><th>gold</th></tr>'+a.scalars.map(c=>'<tr class="'+(c.ok?'ok':'bad')+'"><td>'+c.field+'</td><td>'+fmt(c.pred)+'</td><td>'+fmt(c.gold)+'</td></tr>').join('')+'</table>'+
  '<h3>Reporting owners</h3>'+rowTable(a.tables.reporting_owners)+'<h3>Transactions</h3>'+rowTable(a.tables.transactions)+
  '<details><summary>raw model output</summary><pre>'+esc(d.raw)+'</pre></details>';
 const n=d.note;others=n?(n.other_themes||[]):[];$('theme').value=n?n.theme:(a.themes[0]||'');$('layer').value=n?n.fix_layer:'';$('cause').value=n?n.root_cause:'';$('msg').textContent=n&&n.human_reviewed===false?'noted from the AI pre-review without individual review; saving makes it yours':'';
 if(r)$('usesug').onclick=()=>{const t=r.themes[0];$('theme').value=t.theme;$('layer').value=t.fix_layer;$('cause').value=t.root_cause;others=r.themes.slice(1).map(x=>x.theme)};
 document.querySelectorAll('.item').forEach(e=>e.classList.toggle('on',e.querySelector('small').textContent.startsWith(acc)))}
$('save').onclick=async()=>{const r=await fetch('/api/note/'+cur,{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({theme:$('theme').value,fix_layer:$('layer').value,root_cause:$('cause').value,other_themes:others})});
 if(!r.ok){$('msg').textContent=(await r.json()).detail;return}
 const items=await load();const i=items.findIndex(x=>x.accession===cur);const nx=items.slice(i+1).find(x=>!x.noted)||items.find(x=>!x.noted);
 if(nx)open_(nx.accession);else $('msg').textContent='All noted.'};
$('acceptall').onclick=async()=>{if(!confirm('Write a note for every AI "confirm" suggestion you have not noted yet? They are recorded as NOT individually reviewed.'))return;
 const r=await (await fetch('/api/accept/confirm',{method:'POST'})).json();$('msg').textContent='accepted '+r.accepted+' AI-confirmed notes';await load()};
load().then(items=>items[0]&&open_(items[0].accession));
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


def main() -> None:
    ap = argparse.ArgumentParser(description="Read recorded holdout failures")
    ap.add_argument("--model", default=None, help="model whose recorded answers to read")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    STATE["answers"] = scorer.cache_path(args.model or settings.small_model)
    if not STATE["answers"].exists():
        raise SystemExit(f"no recorded answers at {STATE['answers']}; run the baseline first")
    print(f"reading {STATE['answers'].name}\nopen http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

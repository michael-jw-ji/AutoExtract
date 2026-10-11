"""Review tool for hand-labelling Form 4 filings.

    python scripts/label_form4.py          # then open http://127.0.0.1:8765

You are not labelling from scratch. Each filing is shown next to the gold that
was parsed from its official XML. Your job is to check that gold against what
the page actually says, then either confirm it, correct it, or mark fields
ambiguous. Ambiguity is a first-class answer: if the page and the XML disagree
or a human could reasonably read a field two ways, say so instead of forcing a
label.

One JSON file per filing goes to data/form4/labels/<accession>.json (tracked in
git; it is the one part of the corpus that cannot be regenerated):

  {"accession", "status": "confirmed" | "ambiguous",
   "changed": true if you edited the gold,
   "ambiguous_fields": ["transactions.*.price_per_share", ...],
   "notes": "...", "gold": {...the checked gold...}}

scripts/load_form4.py applies these: labelled filings get your gold and are
placed in the holdout split.

Binds to localhost only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from core.config import settings
from domains import get_domain
from domains.form4_io import features, html_to_text, parse_xml

ROOT = settings.data_dir / "form4"
LABELS = ROOT / "labels"
#: AI pre-review notes (one per filing). Advice only: the human decides.
REVIEWS = ROOT / "review"
TARGET = 30
ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")

#: Tags that make a filing worth a human look, heaviest first.
WEIGHT = {
    "amendment": 5, "multi_owner": 4, "footnoted_price": 3, "holdings_row": 3,
    "derivative": 3, "unusual_code": 3, "multi_class": 3, "indirect": 2,
    "blank_price": 2, "many_rows": 1, "remarks": 1,
}

app = FastAPI()
DOM = get_domain("form4")


def _manifest() -> dict[str, dict]:
    out: dict[str, dict] = {}
    path = ROOT / "manifest.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            out.setdefault(row["accession"], row)
    return out


def _auto(acc: str) -> dict | None:
    """Gold parsed from the XML, or None if it does not validate."""
    try:
        raw = parse_xml((ROOT / "xml" / f"{acc}.xml").read_bytes())
        enriched, _ = DOM.enrich(raw)
        return json.loads(DOM.Schema.model_validate(enriched).model_dump_json())
    except (ValidationError, OSError, ValueError):
        return None


def _review(acc: str) -> dict | None:
    path = REVIEWS / f"{acc}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _label(acc: str) -> dict | None:
    path = LABELS / f"{acc}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _check(acc: str) -> str:
    if not ACC.match(acc):
        raise HTTPException(400, "bad accession")
    return acc


@app.get("/api/queue")
def queue() -> dict:
    items = []
    for acc, meta in _manifest().items():
        if not (ROOT / "xml" / f"{acc}.xml").exists() or _auto(acc) is None:
            continue
        tags = features((ROOT / "xml" / f"{acc}.xml").read_bytes())
        label = _label(acc)
        items.append({
            "accession": acc, "company": meta.get("company", ""), "tags": tags,
            "score": sum(WEIGHT.get(t, 0) for t in tags),
            "status": label["status"] if label else None,
            "review": (_review(acc) or {}).get("verdict"),
        })
    items.sort(key=lambda i: (-i["score"], i["accession"]))
    done = sum(1 for i in items if i["status"])
    return {"items": items, "done": done, "target": TARGET}


@app.get("/api/doc/{acc}")
def doc(acc: str) -> dict:
    acc = _check(acc)
    auto = _auto(acc)
    if auto is None:
        raise HTTPException(404, "no valid gold for this filing")
    body = html_to_text((ROOT / "raw" / f"{acc}.html").read_bytes())
    meta = _manifest().get(acc, {})
    return {
        "accession": acc,
        "url": meta.get("url", ""),
        "text": f"Issuer CIK: {auto['issuer_cik']}\n{body}",
        "tags": features((ROOT / "xml" / f"{acc}.xml").read_bytes()),
        "auto": auto,
        "label": _label(acc),
        "review": _review(acc),
    }


@app.post("/api/doc/{acc}")
def save(acc: str, body: dict) -> dict:
    acc = _check(acc)
    auto = _auto(acc)
    if auto is None:
        raise HTTPException(404, "no valid gold for this filing")
    status = body.get("status")
    if status not in {"confirmed", "ambiguous"}:
        raise HTTPException(400, "status must be confirmed or ambiguous")
    try:
        gold = json.loads(DOM.Schema.model_validate(
            DOM.enrich(body.get("gold") or {})[0]).model_dump_json())
    except ValidationError as exc:
        # Hand labels must satisfy the same schema as every other gold.
        errs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise HTTPException(422, "; ".join(errs[:6]))
    fields = [f.strip() for f in body.get("ambiguous_fields", []) if f.strip()]
    if status == "ambiguous" and not fields:
        raise HTTPException(422, "name at least one ambiguous field")
    LABELS.mkdir(parents=True, exist_ok=True)
    (LABELS / f"{acc}.json").write_text(json.dumps({
        "accession": acc, "status": status, "changed": gold != auto,
        "ambiguous_fields": fields, "notes": str(body.get("notes", "")),
        "gold": gold,
        # Provenance: the label is still a human decision, but if an AI
        # pre-review was on screen when it was made, record that.
        "assisted_by": (_review(acc) or {}).get("reviewer"),
    }, indent=2), encoding="utf-8")
    return {"ok": True, "changed": gold != auto}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Form 4 labelling</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--pane:#f6f6f4;--acc:#1f5fbf;--ok:#1a7f37;--warn:#b35900}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e6e6e6;--mut:#999;--line:#333;--pane:#1f1f1f;--acc:#6ea8fe;--ok:#4cc26b;--warn:#e0a050}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center}
header b{font-size:15px}.bar{flex:1;height:6px;background:var(--line);border-radius:3px;max-width:240px}.bar i{display:block;height:100%;background:var(--ok);border-radius:3px}
main{flex:1;display:grid;grid-template-columns:280px 1fr 1fr;min-height:0}
section{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
h2{margin:0;padding:8px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);border-bottom:1px solid var(--line)}
.scroll{overflow:auto;flex:1}
.item{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.on{background:var(--pane)}
.item small{color:var(--mut);display:block;margin-top:2px}.tag{display:inline-block;font-size:11px;padding:0 5px;border:1px solid var(--line);border-radius:8px;margin:2px 3px 0 0;color:var(--mut)}
.st-confirmed::before{content:"\\2713 ";color:var(--ok)}.st-ambiguous::before{content:"? ";color:var(--warn)}
pre,textarea{margin:0;padding:12px;font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-word;background:var(--pane);color:var(--fg);border:0;flex:1;resize:none;min-height:0;overflow:auto}
footer{padding:10px 16px;border-top:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
input[type=text]{padding:6px 8px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg);min-width:200px}
button{padding:7px 14px;border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:4px;cursor:pointer;font-size:14px}
button.sec{background:none;color:var(--acc)}#rev{padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;max-height:38vh;overflow:auto}#rev:empty{display:none}#rev ul{margin:6px 0;padding-left:18px}#rev li{margin:3px 0}.v-check{color:var(--warn);font-weight:600}.v-looks_right{color:var(--ok);font-weight:600}#rev button{padding:3px 10px;font-size:12px}.rv{font-size:11px;margin-left:4px}#msg{color:var(--warn);flex-basis:100%}a{color:var(--acc)}
@media (max-width:900px){main{grid-template-columns:1fr}}
</style></head><body>
<header><b>Form 4 labelling</b><span id="prog"></span><div class="bar"><i id="fill" style="width:0"></i></div>
<span style="color:var(--mut)">Check the gold (right) against the filing text (middle). Confirm it, fix it, or mark fields ambiguous.</span></header>
<main>
<section><h2>Filings, hardest first</h2><div class="scroll" id="list"></div></section>
<section><h2>Filing text <span id="link"></span></h2><pre id="text"></pre></section>
<section><h2>Gold (editable JSON)</h2><div id="rev"></div><textarea id="gold" spellcheck="false"></textarea></section>
</main>
<footer>
<label><input type="radio" name="st" value="confirmed" checked> gold is right</label>
<label><input type="radio" name="st" value="ambiguous"> ambiguous</label>
<input type="text" id="amb" placeholder="ambiguous fields, e.g. transactions.*.price_per_share">
<input type="text" id="notes" placeholder="notes" style="flex:1">
<button id="save">Save &amp; next</button><button class="sec" id="skip">Skip</button>
<span id="msg"></span></footer>
<script>
let q=[],cur=null;
const $=id=>document.getElementById(id);
async function load(){const r=await (await fetch('/api/queue')).json();q=r.items;
 $('prog').textContent=r.done+' / '+r.target+' labelled';$('fill').style.width=Math.min(100,100*r.done/r.target)+'%';
 $('list').innerHTML='';q.forEach(i=>{const d=document.createElement('div');d.className='item'+(cur===i.accession?' on':'');
  d.innerHTML='<span class="'+(i.status?'st-'+i.status:'')+'">'+i.company.replace(/</g,'&lt;')+'</span>'+(i.review?'<span class="rv v-'+i.review+'">'+(i.review==='check'?'check':'ok?')+'</span>':'')+'<small>'+i.accession+'</small>'+i.tags.map(t=>'<span class="tag">'+t+'</span>').join('');
  d.onclick=()=>open_(i.accession);$('list').appendChild(d);});}
async function open_(acc){cur=acc;const d=await (await fetch('/api/doc/'+acc)).json();
 $('text').textContent=d.text;$('link').innerHTML=d.url?'&middot; <a target="_blank" rel="noopener" href="'+d.url+'">SEC folder</a>':'';
 const r=d.review,esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
 $('rev').innerHTML=r?('<b>AI pre-review:</b> <span class="v-'+r.verdict+'">'+(r.verdict==='check'?'needs your judgement':'looks right')+'</span>'
  +'<ul>'+r.points.map(p=>'<li>'+esc(p)+'</li>').join('')+'</ul>'
  +'Suggests: <b>'+r.suggest.status+'</b>'+(r.suggest.ambiguous_fields.length?' on '+esc(r.suggest.ambiguous_fields.join(', ')):'')
  +' <button class="sec" id="usesug">Use suggestion</button> <small style="color:var(--mut)">You decide; this only fills the form.</small>'):'';
 if(r)$('usesug').onclick=()=>{document.querySelector('input[name=st][value='+r.suggest.status+']').checked=true;
  $('amb').value=r.suggest.ambiguous_fields.join(', ');if(r.suggest.note)$('notes').value=r.suggest.note;};
 const l=d.label;$('gold').value=JSON.stringify(l?l.gold:d.auto,null,2);
 document.querySelector('input[name=st][value='+(l?l.status:'confirmed')+']').checked=true;
 $('amb').value=l?l.ambiguous_fields.join(', '):'';$('notes').value=l?l.notes:'';$('msg').textContent='';
 document.querySelectorAll('.item').forEach(e=>e.classList.toggle('on',e.querySelector('small').textContent===acc));}
function nextUnlabelled(){const i=q.findIndex(x=>x.accession===cur);return q.slice(i+1).concat(q.slice(0,i)).find(x=>!x.status);}
$('save').onclick=async()=>{let gold;try{gold=JSON.parse($('gold').value)}catch(e){$('msg').textContent='Gold is not valid JSON: '+e.message;return}
 const r=await fetch('/api/doc/'+cur,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gold,
  status:document.querySelector('input[name=st]:checked').value,ambiguous_fields:$('amb').value.split(','),notes:$('notes').value})});
 if(!r.ok){$('msg').textContent=(await r.json()).detail;return}
 const prev=cur;await load();const n=nextUnlabelled();if(n)open_(n.accession);else $('msg').textContent='Saved. Nothing left unlabelled in the queue.';};
$('skip').onclick=()=>{const i=q.findIndex(x=>x.accession===cur);if(q[i+1])open_(q[i+1].accession)};
load().then(()=>q[0]&&open_(q[0].accession));
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")

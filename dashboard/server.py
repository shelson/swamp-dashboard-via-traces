#!/usr/bin/env python3
"""Swamp software-factory live dashboard.

Two data sources, one page:
  - OTLP traces (POST /v1/traces, http/json): generic liveness — which
    methods/models are active right now, workflow runs, etc.
  - repo polling (swamp data list/get): authoritative factory state per
    work item (state-<id> entries), plus the state-machine definition.

GET /          factory state-machine view (d3, targeted layout)
GET /api       generic trace-derived activity (runs + misc)
GET /api/factory  factory definition + work-item states + recent activity
"""
import json, os, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WINDOW_S = 600
LIVE_S = 5
FACTORY_MODEL = "demo-factory"
# ponytail: relative to this file — no hardcoded home dir path
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_S = 3

spans = {}                      # spanId -> raw OTLP span dict
lock = threading.Lock()
factory = {"stages": [], "transitions": [], "workItems": [], "activity": [],
           "polledAt": 0, "error": None}
dirty = threading.Event()       # traces for the factory -> poll immediately

def run_swamp(*args):
    r = subprocess.run(["swamp", *args, "--json"], capture_output=True, text=True,
                       cwd=REPO, timeout=30)
    return json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr.strip()[:200]}

def poll_factory():
    definition_at = 0
    while True:
        dirty.wait(POLL_S); dirty.clear()
        try:
            now = time.time()
            if now - definition_at > 30:
                m = run_swamp("model", "get", FACTORY_MODEL)
                ga = m.get("globalArguments", {})
                factory["stages"] = [{"id": s["id"], "description": (s.get("description") or "").strip()}
                                     for s in ga.get("stages", [])]
                tr = [{"name": t["name"], "from": s["id"], "to": t["to"]}
                      for s in ga.get("stages", []) for t in s.get("transitions", [])]
                tr += [{"name": t["name"], "from": "*", "to": t["to"]}
                       for t in ga.get("globalTransitions", [])]
                factory["transitions"] = tr
                definition_at = now
            listing = run_swamp("data", "list", FACTORY_MODEL)
            names = [i["name"] for g in listing.get("groups", [])
                     for i in g.get("items", []) if i["name"].startswith("state-")]
            items = []
            for name in names:
                d = run_swamp("data", "get", FACTORY_MODEL, name)
                c = d.get("content")
                if c:
                    items.append({"id": c["workItem"], "stage": c["stageId"],
                                  "status": c.get("status"), "cycles": c.get("cycles", {}),
                                  "enteredAt": c.get("enteredAt")})
            with lock:
                factory["workItems"] = items
                factory["polledAt"] = now
                factory["error"] = None
        except Exception as e:
            with lock:
                factory["error"] = str(e)

def attrs(s):
    out = {}
    for a in s.get("attributes", []):
        x = a.get("value", {})
        out[a["key"]] = next(iter(x.values()), None) if x else None
    return out

def factory_activity(now_ns):
    evts = []
    for s in list(spans.values()):
        a = attrs(s)
        if a.get("model.name") == FACTORY_MODEL and s["name"] in ("swamp.model.method",):
            evts.append({"method": a.get("method.name"),
                         "ms": int(s.get("endTimeUnixNano") or s.get("startTimeUnixNano", 0)) // 1_000_000})
    evts.sort(key=lambda e: e["ms"], reverse=True)
    return evts[:10]

PAGE = """<!doctype html><meta charset=utf-8><title>demo-factory</title>
<style>
body{font:14px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;display:flex;height:100vh}
#main{flex:1;padding:1em}
#side{width:280px;border-left:1px solid #30363d;padding:1em;overflow:auto}
h1{font-size:16px;margin:.2em 0}
h2{font-size:13px;color:#8b949e;margin:1em 0 .3em}
.node rect{fill:#161b22;stroke:#30363d;stroke-width:2;rx:8}
.node text{fill:#e6edf3;font-size:13px;font-weight:600}
.node .count{fill:#8b949e;font-size:11px;font-weight:400}
.node.hot rect{stroke:#f0883e}
.link{fill:none;stroke:#3fb95044;stroke-width:2}
.link.rework{stroke:#d2992244}
.token circle{stroke:#0d1117;stroke-width:2}
.token text{fill:#0d1117;font-size:10px;font-weight:700;text-anchor:middle}
.ev{border-left:2px solid #30363d;padding-left:8px;margin:.4em 0;color:#8b949e}
.err{color:#f85149}
</style>
<div id=main><h1>demo-factory <span id=t style="color:#8b949e;font-weight:400"></span></h1><svg id=svg></svg></div>
<div id=side><h2>work items</h2><div id=items></div><h2>recent factory calls</h2><div id=events></div></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
const W=()=>document.getElementById('main').clientWidth-32, H=()=>innerHeight-80;
// targeted layout: hand-placed per known stage id, fallback = grid
const POS={planning:[.08,.5],"plan-review":[.3,.28],implementing:[.52,.5],testing:[.72,.28],"code-review":[.88,.5],done:[.97,.82],aborted:[.3,.82]};
let prev={}, edges=[], first=true;
const svg=d3.select('#svg');
function layout(stages){
  return stages.map((s,i)=>{const p=POS[s.id]||[.1+.8*(i%4)/3,.2+.6*Math.floor(i/4)/2];return{...s,x:p[0]*W(),y:p[1]*H()}});
}
function edgePath(a,b,i){
  const dx=b.x-a.x, dy=b.y-a.y, bend=(a.from===b.to||i>0)?.35:.15;
  const mx=(a.x+b.x)/2-dy*bend, my=(a.y+b.y)/2+dx*bend;
  return `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`;
}
function redraw(d,changed){
  svg.attr('width',W()).attr('height',H());
  const nodes=layout(d.stages), byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
  svg.selectAll('*').remove();
  const defs=svg.append('defs');
  defs.append('marker').attr('id','arr').attr('viewBox','0 0 10 10').attr('refX',9).attr('refY',5)
    .attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto')
    .append('path').attr('d','M0,0L10,5L0,10z').attr('fill','#8b949e');
  edges=[];
  const g=svg.append('g');
  d.transitions.forEach((t,i)=>{
    const a=t.from==='*'?null:byId[t.from], b=byId[t.to];
    if(!a||!b) return;
    const path=g.append('path').attr('class','link '+(t.name==='rework'||t.name==='fail'||t.name==='abort'?'rework':''))
      .attr('d',edgePath(a,b,i)).attr('marker-end','url(#arr)');
    edges.push({...t,path:path.node()});
  });
  const groups={};
  d.workItems.forEach(w=>{(groups[w.stage]??=[]).push(w)});
  const node=g.selectAll('.node').data(nodes).join('g').attr('class','node')
    .attr('transform',n=>`translate(${n.x},${n.y})`);
  node.append('rect').attr('x',-60).attr('y',-24).attr('width',120).attr('height',48).attr('rx',8);
  node.append('text').attr('text-anchor','middle').attr('dy',-4).text(n=>n.id);
  node.append('text').attr('class','count').attr('text-anchor','middle').attr('dy',12)
    .text(n=>(groups[n.id]||[]).length? groups[n.id].length+' item(s)':'');
  const hot=new Set(d.activity.filter(a=>Date.now()-a.ms<5000).map(a=>a.method));
  if(d.activity.length&&Date.now()-d.activity[0].ms<5000)
    d.workItems.forEach(w=>{const n=byId[w.stage]; if(n) g.append('circle').attr('cx',n.x).attr('cy',n.y)
      .attr('r',70).attr('fill','none').attr('stroke','#f0883e').attr('opacity',.6)
      .transition().duration(1200).attr('r',110).attr('opacity',0).remove();});
  Object.entries(groups).forEach(([sid,ws])=>{
    const n=byId[sid]; if(!n) return;
    ws.forEach((w,i)=>{
      const x=n.x-((ws.length-1)*13)+i*26, y=n.y-38;
      const tok=g.append('g').attr('class','token');
      const p=prev[w.id];
      if(p&&p!==sid&&edges.length){
        const e=edges.find(e=>e.from===p&&e.to===sid)||edges[0], L=e.path.getTotalLength();
        tok.attr('transform',()=>{const pt=e.path.getPointAtLength(0);return`translate(${pt.x},${pt.y})`})
          .transition().duration(1400).ease(d3.easeCubicInOut)
          .attrTween('transform',()=>t=>{const pt=e.path.getPointAtLength(t*L);return`translate(${pt.x},${pt.y})`;});
      } else tok.attr('transform',`translate(${x},${y})`);
      tok.append('circle').attr('r',11).attr('fill',w.status==='active'?'#3fb950':(w.status==='done'?'#58a6ff':'#8b949e'));
      tok.append('text').attr('dy',3.5).text(w.id.replace(/^(DEMO-|demo-)/,''));
    });
  });
  prev=Object.fromEntries(d.workItems.map(w=>[w.id,w.stage]));
  document.getElementById('t').textContent=' · updated '+new Date().toLocaleTimeString()+(d.error?' · poll error: '+d.error:'');
  document.getElementById('items').innerHTML=d.workItems.map(w=>
    `<div class=ev><b style="color:#e6edf3">${w.id}</b> → ${w.stage} <span class=err>${w.status!=='active'?w.status:''}</span><br>cycle ${w.cycles[w.stage]||1} · since ${(w.enteredAt||'').slice(11,19)}</div>`).join('')||'<i>none yet</i>';
  document.getElementById('events').innerHTML=d.activity.map(a=>
    `<div class=ev>${a.method} <span style="color:#484f58">${new Date(a.ms).toLocaleTimeString()}</span></div>`).join('')||'<i>—</i>';
}
async function tick(){
  try{ redraw(await (await fetch('/api/factory')).json()); }catch(e){}
}
addEventListener('resize',()=>{prev={};tick()});
setInterval(tick,1500); tick();
</script>"""

def summarize_generic(now_ns):
    traces = {}
    for s in list(spans.values()):
        if now_ns - int(s.get("endTimeUnixNano") or s.get("startTimeUnixNano") or 0) > WINDOW_S * 1e9:
            del spans[s["spanId"]]; continue
        traces.setdefault(s["traceId"], []).append(s)
    runs, misc = [], []
    for tid, tspans in traces.items():
        tspans.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))
        live = any(now_ns - int(s.get("endTimeUnixNano") or s.get("startTimeUnixNano") or 0) < LIVE_S * 1e9 for s in tspans)
        run = next((s for s in tspans if s["name"] == "swamp.workflow.run"), None)
        if run:
            a = attrs(run)
            jobs = {}
            for s in tspans:
                sa = attrs(s)
                if s["name"] == "swamp.workflow.job":
                    jobs.setdefault(sa.get("job.name"), {"name": sa.get("job.name"), "status": sa.get("job.status"), "steps": []})
                elif s["name"] == "swamp.workflow.step":
                    j = jobs.setdefault(sa.get("job.name"), {"name": sa.get("job.name"), "status": None, "steps": []})
                    dur = int(s.get("endTimeUnixNano", 0) or 0) - int(s.get("startTimeUnixNano", 0) or 0)
                    if not any(x["name"] == sa.get("step.name") and x["durNs"] for x in j["steps"]):
                        j["steps"].append({"name": sa.get("step.name"), "status": sa.get("step.status"),
                                           "durNs": dur if s.get("endTimeUnixNano") else None})
            runs.append({"traceId": tid, "workflow": a.get("workflow.name", "?"),
                         "runId": a.get("workflow.run_id", tid), "jobs": list(jobs.values()),
                         "live": live, "startMs": int(run.get("startTimeUnixNano", 0)) // 1_000_000})
        else:
            for s in tspans:
                if s["name"] in ("swamp.cli", "swamp.model.method"):
                    dur = int(s.get("endTimeUnixNano", 0) or 0) - int(s.get("startTimeUnixNano", 0) or 0)
                    misc.append({"name": s["name"], "attrs": attrs(s),
                                 "durNs": dur if s.get("endTimeUnixNano") else None,
                                 "startMs": int(s.get("startTimeUnixNano", 0)) // 1_000_000})
    runs.sort(key=lambda r: r["startMs"], reverse=True)
    misc.sort(key=lambda m: m["startMs"], reverse=True)
    return {"now": time.time() * 1000, "runs": runs[:20], "misc": misc[:30]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, ct):
        self.send_response(200); self.send_header("content-type", ct)
        self.send_header("content-length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/api/factory":
            with lock:
                out = dict(factory)
            out["activity"] = factory_activity(time.time_ns())
            self.send(json.dumps(out).encode(), "application/json")
        elif self.path == "/api":
            self.send(json.dumps(summarize_generic(time.time_ns())).encode(), "application/json")
        else:
            self.send(PAGE.encode(), "text/html")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path.startswith("/v1/traces"):
            try:
                for rs in json.loads(body).get("resourceSpans", []):
                    for ss in rs.get("scopeSpans", []):
                        for sp in ss.get("spans", []):
                            spans[sp["spanId"]] = sp
                            if attrs(sp).get("model.name") == FACTORY_MODEL:
                                dirty.set()
            except Exception as e:
                print("parse error:", e, flush=True)
        self.send(b"{}", "application/json")

threading.Thread(target=poll_factory, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 4319), H).serve_forever()

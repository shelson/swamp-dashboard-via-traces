#!/usr/bin/env python3
"""Drive a demo-factory work item to done, satisfying gates as they appear.

    python3 dashboard/drive.py DEMO-2 [--fast]

Useful for generating trace/state traffic for the dashboard.
"""
import json, os, re, subprocess, sys, time

WI = sys.argv[1]
FAST = "--fast" in sys.argv
FLAKY = "--flaky" in sys.argv
flaked = set()
# ponytail: relative to this file — no hardcoded home dir path
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def swamp(*args, stdin=None):
    r = subprocess.run(["swamp", "model", "method", "run", "demo-factory", *args,
                        "--input", f"workItem={WI}", "--json"],
                       input=stdin, capture_output=True, text=True, cwd=REPO, timeout=60)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "error": (r.stderr or r.stdout)[-300:]}

PAYLOADS = {
    "plan": '{"summary":"demo plan","steps":[{"description":"do thing","files":[]}],"testingStrategy":"none"}',
    "plan-review": '{"findings":[]}',
    "change-summary": '{"summary":"demo change","headSha":"abc1234"}',
    "code-review": '{"findings":[]}',
    "branch-info": '{"branch":"demo","headSha":"abc1234"}',
    "test-run": '{"status":"succeeded","suite":"demo","passed":1}',
}

def record(name):
    payload = PAYLOADS.get(name, '{"demo":true}')
    r = swamp("record_artifact", "--input", f"name={name}", "--input", f"payload={payload}")
    if r.get("status") != "succeeded":
        r = swamp("record_evidence", "--input", f"name={name}", "--input", f"payload={payload}")
    return r

def record_blocking(name):
    return swamp("record_artifact", "--input", f"name={name}", "--input",
                 'payload={"findings":[{"severity":"critical","description":"demo blocking finding"}]}')

def pause():
    if not FAST: time.sleep(3)

swamp("start")
for _ in range(40):
    st = swamp("status")
    try:
        a = st["dataArtifacts"][0]["attributes"]
    except Exception:
        print("status failed:", st.get("error")); sys.exit(1)
    stage = a["stage"]["id"]
    print(f"{WI}: {stage} ({a['status']})", flush=True)
    if a["status"] != "active" or stage in ("done", "aborted"):
        break
    choices = [t for t in a["transitions"] if t["name"] != "abort"]
    if FLAKY and stage in ("plan-review", "code-review") and stage not in flaked:
        flaked.add(stage)
        swamp("record_dispatch")
        record_blocking(stage)
        t = next((t for t in choices if t["name"] == "rework"), None)
        print(f"  sabotaged {stage} with a critical finding, taking rework", flush=True)
    else:
        t = choices[0] if choices else None
    if not t:
        print("no transition available"); sys.exit(1)
    if not t["satisfied"]:
        swamp("record_dispatch")
        for g in t.get("gates", []):
            if g.get("pass"): continue
            for reason in g.get("reasons", []):
                if m := re.search(r"(?:artifact|evidence) '([^']+)'", reason):
                    r = record(m.group(1))
                    if r.get("status") != "succeeded":
                        print(f"  record {m.group(1)} failed: {str(r.get('error'))[:120]}", flush=True)
                elif m := re.search(r"approval '([^']+)'", reason):
                    swamp("approve", "--input", f"gateId={m.group(1)}", "--input", "actor=drive.py")
        pause()
    swamp("record_dispatch")
    r = swamp("advance", "--input", f"transition={t['name']}")
    print(f"  -> {t['name']}: {r.get('status')}", flush=True)
    if r.get("status") != "succeeded":
        print("   ", str(r.get("error"))[:200], flush=True)
    pause()
print("final stage:", stage)

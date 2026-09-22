# Trace-driven swamp activity dashboard (POC)

`server.py` is a stdlib-only Python OTLP receiver + live page.
It listens on the standard OTLP HTTP port **4318**: `POST /v1/traces` collects
spans, `GET /` serves a live run-centric view (workflow runs → jobs → steps),
`GET /api` returns the same as JSON. No history; 10-minute rolling window.

    python3 dashboard/server.py &

Point swamp at it (http/json keeps the receiver dependency-free):

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
    swamp workflow run demo

Open http://localhost:4318/ — the landing page is now the **demo-factory
state machine** (d3, hand-placed layout): work-item tokens sit in their stage
and animate along transition edges when they move; recent factory method
calls pulse. `GET /api/factory` is the JSON behind it.

Factory state is authoritative from the repo: the server polls
`swamp data list/get demo-factory` every 3s (`state-<workItem>` entries) and
`swamp model get` every 30s for stages/transitions. Traces only add liveness
and trigger an immediate re-poll. Generic trace view: `GET /api`.

Generate test traffic (walks a work item to `done`, auto-satisfying gates):

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
    python3 dashboard/drive.py DEMO-4

Run several in parallel in different terminals for the multi-item view.
Spans carry `workflow.name`, `workflow.run_id`, `job.name`, `step.name`,
`step.status`, `model.name`, `method.name` — enough to rebuild the DAG live.

Known limits (trace-side, not dashboard bugs):

- A step is only visible once it *finishes* (exporters flush completed spans),
  so "currently executing step" can't be shown — only steps completed so far
  inside a live run.
- Multi-repo/multi-user: add resource `service.name`/host attrs later; here
  traces are only separated by traceId.

# swamp-dashboard-via-traces

POC for a realtime swamp software-factory dashboard powered by OTLP traces and repo polling.

## What

`dashboard/server.py` is a stdlib-only Python HTTP server that renders a live D3 state-machine view of swamp factory runs. It combines two data sources:

- **OTLP traces** (`POST /v1/traces`, http/json) — liveness feed from `swamp workflow run`
- **Repo polling** (`swamp data list/get`) — authoritative factory state per work item

## Quickstart

```bash
# Start dashboard
python3 dashboard/server.py &

# In other terminals, drive work items through the factory:
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
python3 dashboard/drive.py DEMO-1

OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
python3 dashboard/drive.py DEMO-2
```

Open http://localhost:4318/ — the state machine shows work items moving through stages with animated transitions.

## Endpoints

| Path | Description |
|------|-------------|
| `GET /` | Factory state-machine dashboard (D3) |
| `GET /api` | Generic trace-derived activity (runs + misc spans) |
| `GET /api/factory` | Factory definition + work-item states + recent activity (JSON) |
| `POST /v1/traces` | OTLP trace ingestion |

## Demo models

The swamp models in this repo (`demo-factory`, `demo` workflow) are noop demos — they exist solely to generate trace/state traffic for testing the dashboard. They don't do real factory work.
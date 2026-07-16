# AI-Assisted Root Cause Analysis

This module watches `iot.device_logs` for new error/critical events and
asks a local LLM (Ollama) to diagnose the device and propose fixes,
grounded in your error source code and resolution documentation.

## What it does

```
iot.device_logs  --(new error/critical row)-->  RCATrigger
                                                       |
                                                       v
                                                 RCAAnalyzer
                                                  |        |
                                       KnowledgeBase    OllamaLLM
                                       (./kb)           (M3 512k ctx)
                                                  |        |
                                                  v        v
                                              Finding --persist--> ai.rca_findings
```

The dashboard's existing WebSocket poller keeps streaming events to the
UI. The AI trigger runs in parallel and enriches each event with a
verdict, which the dashboard can fetch via the new endpoint
`GET /api/ai/findings/{log_id}`.

## Files

| File | Purpose |
|------|---------|
| `analyzer.py` | LLM client, KB retriever, prompt builder, JSON parser. |
| `trigger.py`  | DB poller, schema bootstrap, persistence. |
| `kb/`         | Your knowledge base (errors + sources). See layout below. |
| `run.py`      | Standalone entry point for ops/tests. |

## Knowledge base layout

Drop files into `topology/ai/kb/`. The KB is best-effort keyword
retrieval — no embeddings required to start.

```
topology/ai/kb/
├── errors/                      # resolution docs (.md or .txt)
│   ├── CISCO_AP_OFFLINE.md
│   ├── NVR_DISK_FULL.md
│   └── CAMERA_AUTH_FAILED.md
└── sources/                     # error code dumps, source snippets
    ├── ap_firmware_3.2.1_snippet.c
    └── onvif_error_codes.txt
```

Each file becomes one chunk. The retriever scores chunks by token
overlap with the event signature (`status_code + status + severity +
device_type + message`). Resolution docs are weighted higher than raw
source dumps.

## Running

```bash
# 1. Make sure Ollama is running with the Modelfile in this repo:
ollama create topology-rca -f Modelfile
ollama serve

# 2. Start the trigger alongside the FastAPI app
python -m ai.run
```

Or wire it into the existing FastAPI lifespan in
`backend/main.py` (see snippet below).

## FastAPI integration

In `backend/main.py`, add the trigger to the lifespan:

```python
from ai import RCAAnalyzer, KnowledgeBase, RCATrigger
from backend.database import pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    analyzer = RCAAnalyzer(KnowledgeBase(Path(__file__).parent.parent / "ai" / "kb"))
    trigger  = RCATrigger(analyzer, pool)
    trigger.start()
    yield
    trigger.stop()
```

## Querying findings

```sql
-- Latest 20 verdicts
SELECT TOP 20 f.finding_id, f.log_id, f.device_id, f.confidence,
       LEFT(f.root_cause, 200) AS root_cause, f.created_at
FROM ai.rca_findings f
ORDER BY f.created_at DESC;
```

## Cascade / root-cause detection

When several devices go down inside a short window (default: 5
minutes, 3+ devices), the trigger calls `CascadeCorrelator`, which:

1. Loads the active topology (`iot.v_active_topology`) into an
   in-memory adjacency list. Direction: `source` provides service to
   `target`, so failures flow *to* `target`.
2. Builds a `FailingDevice` for each device that produced an
   `error`/`critical` event (or any `offline`/`down`/`failed` status)
   inside the window.
3. Scores every failing device as a candidate root cause by
   combining:
   - **topology coverage**  (65%): how many of the other failing
     devices lie in its downstream blast radius. This is the main
     signal.
   - **temporal lead**      (25%): how much earlier than the cluster
     mean its first failure landed.
   - **severity**           (10%): critical/offline > error > warning.
4. Persists the winning cascade to `ai.cascades` and broadcasts a
   `cascade` WebSocket message with the top 5 candidates and their
   score breakdowns.

The dashboard renders each cascade as a card under **AI Root-Cause
Cascades** showing the suspect device, its confidence, the human
explanation, and the affected/candidate lists.

### Tuning

Pass a custom correlator when wiring the trigger:

```python
from ai import CascadeCorrelator, TopologyGraph
graph = TopologyGraph(load_edges_from_db())  # list[(src, dst)]
correlator = CascadeCorrelator(
    graph,
    cluster_threshold=4,    # require 4+ failing devices
    window_minutes=10,      # wider window
)
trigger = RCATrigger(analyzer, pool, correlator=correlator)
```

### Querying cascades

```sql
-- Latest 10 cascades with their root cause + confidence
SELECT TOP 10 cascade_id, detected_at, cluster_size,
       root_cause_device_id, root_cause_confidence, explanation
FROM ai.cascades
ORDER BY detected_at DESC;
```

Or via the API: `GET /api/ai/cascades?limit=50` (and
`GET /api/ai/cascades/{cascade_id}` for full detail).

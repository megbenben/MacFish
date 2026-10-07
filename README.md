<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MacFish Logo" width="70%"/>

简洁通用的群体智能引擎，预测万物 —— 可直接使用 DeepSeek API 运行
</br>
<em>A Simple and Universal Swarm Intelligence Engine, Predicting Anything</em>
</br>
<sub>Forked from <a href="https://github.com/666ghj/MiroFish">MiroFish</a></sub>

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

---

## 🔧 What this fork changes

This fork rewrites the pipeline around **one direct LLM provider** and **local storage**, and
fixes a set of correctness problems found by auditing the original code. Everything below has
been verified locally with an offline test suite (no LLM spend).

### 1. Direct DeepSeek API — no Zep Cloud, no external graph database

The knowledge graph used to depend on Zep Cloud, which meant a second vendor, a second API key,
and a hard startup failure when that key was missing. It is now **fully local**:

- Extraction runs on **DeepSeek** (`deepseek-chat`) via the plain OpenAI-compatible API
- Graph storage is a **local SQLite file** (`backend/uploads/graphs.db`)
- `ZEP_API_KEY` is **not required** — and is no longer part of startup validation
- Any OpenAI-compatible endpoint works by changing `LLM_BASE_URL` / `LLM_MODEL_NAME`

### 2. Multi-format document ingest

Seed material is no longer limited to PDF / Markdown / TXT:

| Category | Formats |
|---|---|
| Documents | `.pdf` (text layer, optional OCR for scanned pages), `.docx`, `.pptx`, `.xlsx`, `.xlsm` |
| Text | `.txt`, `.md`, `.markdown`, `.csv` |
| Images | `.png`, `.jpg`, `.jpeg`, `.heic`, `.heif`, `.tiff`, `.tif`, `.bmp`, `.webp`, `.gif` (via OCR) |

- OCR uses the **local macOS Vision framework** — free, offline, no API key
  (`uv sync --extra ocr-macos`)
- An optional vision-LLM backend can be configured instead
- Legacy binary Office formats (`.doc` / `.ppt` / `.xls`) are rejected with an explicit
  "save as .docx/.pptx/.xlsx" message rather than failing silently
- Per-file parse results are reported back, so a single bad file no longer discards the batch

### 3. Engineering fixes from the code audit

| Area | What changed |
|---|---|
| Graph duplication | Node identity is now `(graph_id, name)` instead of a fresh `uuid4()` per extraction. Repeated rebuilds used to re-insert every entity — one real database had **9336 node rows for 960 distinct names**. A repair script (`scripts/dedupe_graph.py`) migrates existing databases |
| Simulation lifecycle | A stalled simulation no longer hangs at "running" forever; orphan child processes from a backend restart are detected and reaped; stopping a run is no longer reported as a failure |
| Cost guardrails | Persona generation and simulation startup both warn with an estimated call count before spending anything over the configured budget |
| Task persistence | Long-running tasks survive a backend restart instead of leaving the UI spinning |
| Security defaults | The backend binds `127.0.0.1` by default and CORS allows only the local dev origin |
| Test suite | `pytest` went from **0 tests to 92**, plus an offline benchmark harness (39 checks), a multi-format ingest check (11 cases), and a frontend markdown check (18 cases) |

## ⚡ Overview

**MacFish** is a multi-agent prediction engine. By extracting seed information from the real world
(breaking news, policy drafts, financial signals, or any documents), it builds a parallel digital
world where many agents with independent personas and memory interact on simulated social
platforms. You can inject variables mid-run and observe how the scenario unfolds.

> **You provide:** seed documents + a prediction requirement in natural language
> **MacFish returns:** a detailed prediction report, plus an interactive simulated world

## 🔄 Workflow

1. **Graph Building** — document parsing, entity/relation extraction, GraphRAG construction
2. **Environment Setup** — persona generation and agent configuration
3. **Simulation** — parallel Twitter/Reddit simulation with temporal memory updates
4. **Report Generation** — ReportAgent with a retrieval toolset
5. **Deep Interaction** — chat with any agent in the simulated world, or with the ReportAgent

## 🚀 Quick Start

### Prerequisites

| Tool | Version | Check |
|---|---|---|
| **Node.js** | 18+ | `node -v` |
| **Python** | ≥3.11, ≤3.12 | `python --version` |
| **uv** | latest | `uv --version` |

> The backend needs Python ≥3.11. If your system Python is older, `uv` can install and manage
> 3.12 for you: `uv python install 3.12`.

### 1. Configure environment

```bash
cp .env.example .env
# then edit .env and fill in your DeepSeek key
```

```env
LLM_PROVIDER=network
LLM_API_KEY=sk-your_deepseek_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat
```

> **No Zep Cloud key required.** The knowledge graph is local SQLite.
> Any OpenAI-compatible provider works — point `LLM_BASE_URL` / `LLM_MODEL_NAME` at it.

### 2. Install dependencies

```bash
npm run setup:all
```

Or step by step: `npm run setup` (Node) and `npm run setup:backend` (Python).

Optional, for local OCR of images and scanned PDFs (macOS only):

```bash
cd backend && uv sync --extra ocr-macos
```

### 3. Start

```bash
npm run dev
```

- Frontend: <http://localhost:3000>
- Backend API: <http://localhost:5001>

Individually: `npm run backend` / `npm run frontend`.

### Docker

```bash
cp .env.example .env
docker compose up -d
```

## 📖 Usage

### Step 1 — Create a project
Open <http://localhost:3000>, upload seed documents (see the format table above) or paste text,
and describe your prediction requirement.

### Step 2 — Generate the ontology
DeepSeek analyses the material and proposes entity types (Person, Organization, Event, Concept…)
and relation types. Review and adjust before confirming.

### Step 3 — Build the knowledge graph
Documents are chunked and sent to DeepSeek for entity/relation extraction; results land in
`backend/uploads/graphs.db`. Browse the nodes and edges in the UI.

### Step 4 — Run the simulation
Personas are generated from graph entities, then agents interact autonomously on simulated
Twitter/Reddit platforms. Agent actions are written back to the graph as temporal memory.

### Step 5 — Generate the report
The Report Agent retrieves from the knowledge graph and writes an analysis report, including a
scenario tree. You can then chat with any agent, or with the Report Agent itself.

### Data storage

| Data | Location |
|---|---|
| Knowledge graph | `backend/uploads/graphs.db` |
| Uploaded files & projects | `backend/uploads/projects/` |
| Simulation runs | `backend/uploads/simulations/` |
| Reports | `backend/uploads/reports/` |
| Runtime settings (contains your key) | `macfish_settings.json` (gitignored) |

### Troubleshooting

| Problem | Solution |
|---|---|
| DeepSeek quota exhausted | Point `LLM_BASE_URL` / `LLM_MODEL_NAME` at another OpenAI-compatible provider |
| Poor extraction quality | Refine the entity/relation types during ontology generation |
| Simulation too slow | Lower `OASIS_DEFAULT_MAX_ROUNDS` in `.env` (default 10) |
| Simulation stuck at "running" | Check for orphaned runner processes; the runner now detects stalls and reclaims orphaned children on startup |
| Entity count looks capped | Graphs larger than the read limit are truncated; the UI now says so explicitly |
| Reset the graph | Rebuild with "delete and rebuild" in Step 1, or remove `backend/uploads/graphs.db` |

## 🧪 Tests

```bash
cd backend
uv run pytest tests -q                              # 92 tests, offline
uv run python scripts/run_benchmark.py verify       # 39 checks, stub driver
uv run python scripts/test_multi_format.py          # 11 format cases
```

```bash
cd frontend
node scripts/check-markdown.mjs                     # 18 cases
```

All of these run without network access and without spending LLM calls.

## 📚 Documentation

- [Architecture](./docs/ARCHITECTURE.md) — pipeline, storage layout, process model
- [Manual](./docs/MANUAL.md) — install, configure, run a full prediction, troubleshooting

## 📄 Acknowledgments

- Simulation engine: **[OASIS](https://github.com/camel-ai/oasis)** — thanks to the CAMEL-AI team
- Upstream project: **[MiroFish](https://github.com/666ghj/MiroFish)**

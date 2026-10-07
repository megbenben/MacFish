<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MacFish Logo" width="70%"/>

简洁通用的群体智能引擎，预测万物 —— 可直接使用 DeepSeek API 运行
</br>
<em>A Simple and Universal Swarm Intelligence Engine, Predicting Anything</em>

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

---

## ✨ What's new in this fork

### 1. Direct DeepSeek API — no Zep Cloud, no external graph database

The knowledge graph used to depend on Zep Cloud: a second vendor, a second API key, and a hard
startup failure when that key was missing. It is now **fully local**:

- Extraction runs on **DeepSeek** (`deepseek-chat`) through the plain OpenAI-compatible API
- Graph storage is a **local SQLite file** (`backend/uploads/graphs.db`)
- `ZEP_API_KEY` is **not required** and is no longer part of startup validation
- Any OpenAI-compatible endpoint works — change `LLM_BASE_URL` / `LLM_MODEL_NAME`

### 2. Multi-format document ingest

Seed material is no longer limited to PDF / Markdown / TXT:

| Category | Formats |
|---|---|
| Documents | `.pdf` (text layer, optional OCR for scanned pages), `.docx`, `.pptx`, `.xlsx`, `.xlsm` |
| Text | `.txt`, `.md`, `.markdown`, `.csv` |
| Images | `.png`, `.jpg`, `.jpeg`, `.heic`, `.heif`, `.tiff`, `.tif`, `.bmp`, `.webp`, `.gif` (via OCR) |

- OCR uses the **local macOS Vision framework** — free, offline, no API key
  (`uv sync --extra ocr-macos`), with an optional vision-LLM backend
- Legacy binary Office formats (`.doc` / `.ppt` / `.xls`) are rejected with an explicit
  "save as .docx/.pptx/.xlsx" message instead of failing silently
- Per-file parse results are reported back, so one bad file no longer discards the batch

### 3. Engineering highlights

This fork was audited end to end and rebuilt around verifiable behaviour rather than
"it seems to work":

| Highlight | Detail |
|---|---|
| **Graph de-duplication** | Node identity is now `(graph_id, name)` instead of a fresh `uuid4()` per extraction. Repeated rebuilds used to re-insert every entity — one real database had **9336 node rows for 960 distinct names** (`SAP` alone had 440 copies). A migration script (`scripts/dedupe_graph.py`) repairs existing databases, and rebuilds no longer accumulate |
| **Verifiable offline** | `pytest` went from **0 to 92 tests**, plus a backtest harness (39 checks with a stub driver), a multi-format ingest check (11 cases) and a frontend markdown check (18 cases). All of it runs **without network access and without spending a single LLM call** |
| **Cost guardrails** | Both persona generation and simulation startup estimate their LLM call count first and stop for confirmation when the configured budget is exceeded — instead of silently issuing thousands of calls |
| **Reproducible runs** | Every report carries a run manifest (commit, inputs, model), and reports include a scenario tree rather than a single flat narrative |
| **Robust simulation lifecycle** | A stalled run is detected instead of hanging at "running" forever; orphaned runner processes from a backend restart are reclaimed on startup; stopping a run is no longer reported as a failure |
| **Crash-safe task tracking** | Long-running tasks persist to disk and survive a backend restart, so the UI never spins forever |
| **Safe defaults** | The backend binds `127.0.0.1` by default and CORS allows only the local dev origin |
| **Guarded input handling** | Uploaded chunking parameters are validated (a degenerate `chunk_overlap` used to send a background thread into an unbounded loop), and rendered report content is HTML-escaped before injection |

## 🏗 How it works

```
documents ─► ontology ─► knowledge graph ─► personas ─► simulation ─► report ─► chat
             (LLM)        (local SQLite)      (LLM)      (OASIS)      (LLM)
```

| Stage | What happens |
|---|---|
| 1. Ontology | DeepSeek reads the seed material and proposes entity / relation types |
| 2. Graph build | Documents are chunked, entities and relations are extracted, results land in SQLite |
| 3. Personas | Every graph entity becomes an agent with a generated persona |
| 4. Simulation | Agents interact autonomously on simulated Twitter / Reddit, with temporal memory written back to the graph |
| 5. Report | A Report Agent retrieves from the graph and writes the analysis, including a scenario tree |
| 6. Interaction | Chat with any agent in the simulated world, or with the Report Agent |

**Process model:** Flask (port 5001) serves the API and supervises each simulation as a
**separate child process**; the frontend is a Vite app (port 3000). Mid-run event injection and
agent interviews travel over a file-based IPC channel (`ipc_commands/` / `ipc_responses/`).

**Storage** — everything lives under `backend/uploads/`:

| Data | Location |
|---|---|
| Knowledge graph | `graphs.db` (SQLite) |
| Uploaded files & projects | `projects/` |
| Simulation runs | `simulations/` |
| Reports | `reports/` |
| Runtime settings (contains your key) | `macfish_settings.json` (gitignored) |

## 🚀 Quick Start

### Prerequisites

| Tool | Version | Check |
|---|---|---|
| **Node.js** | 18+ | `node -v` |
| **Python** | ≥3.11, ≤3.12 | `python --version` |
| **uv** | latest | `uv --version` |

> The backend needs Python ≥3.11. If your system Python is older, `uv` can manage 3.12 for you:
> `uv python install 3.12`.

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

> **No Zep Cloud key required** — the knowledge graph is local SQLite.
> Any OpenAI-compatible provider works; point `LLM_BASE_URL` / `LLM_MODEL_NAME` at it.

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

1. **Create a project** — open <http://localhost:3000>, upload seed documents (see the format
   table above) or paste text, and describe your prediction requirement.
2. **Generate the ontology** — DeepSeek proposes entity and relation types; review and adjust.
3. **Build the knowledge graph** — documents are chunked and extracted into `graphs.db`.
   Rebuilding a graph asks first, because rebuilding replaces the previous one.
4. **Run the simulation** — personas are generated from graph entities, then agents interact
   autonomously on the simulated platforms. You can inject events mid-run.
5. **Generate the report** — the Report Agent writes the analysis and scenario tree; then chat
   with any agent or with the Report Agent itself.

### Troubleshooting

| Problem | Solution |
|---|---|
| DeepSeek quota exhausted | Point `LLM_BASE_URL` / `LLM_MODEL_NAME` at another OpenAI-compatible provider |
| Poor extraction quality | Refine the entity / relation types during ontology generation |
| Simulation too slow | Lower `OASIS_DEFAULT_MAX_ROUNDS` in `.env` (default 10) |
| Simulation stuck at "running" | The runner now detects stalls and reclaims orphaned child processes on startup |
| Entity count looks capped | Graphs larger than the read limit are truncated, and the UI now says so explicitly |
| Reset the graph | Rebuild with "delete and rebuild", or remove `backend/uploads/graphs.db` |

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

None of these touch the network or spend LLM calls.

## 📄 Reference

Forked from **[MiroFish](https://github.com/666ghj/MiroFish)**.

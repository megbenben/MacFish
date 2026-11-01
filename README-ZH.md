<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MacFish Logo" width="70%"/>

简洁通用的群体智能引擎，预测万物 —— 可直接使用 DeepSeek API 运行
</br>
<em>A Simple and Universal Swarm Intelligence Engine, Predicting Anything</em>

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

---

## ✨ 这个 fork 带来了什么

### 一、直连 DeepSeek API —— 不再依赖 Zep Cloud 与外部图数据库

知识图谱原本依赖 Zep Cloud：第二个供应商、第二个 API Key，而且那个 Key 一旦缺失就
**直接起不来**。现在完全本地化：

- 抽取由 **DeepSeek**（`deepseek-chat`）通过标准 OpenAI 兼容接口完成
- 图谱存储是**本地 SQLite 文件**（`backend/uploads/graphs.db`）
- **不再需要 `ZEP_API_KEY`**，它也已从启动校验中移除
- 任何 OpenAI 兼容接口都能换，改 `LLM_BASE_URL` / `LLM_MODEL_NAME` 即可

### 二、多格式文档摄入

种子材料不再限于 PDF / Markdown / TXT：

| 类别 | 格式 |
|---|---|
| 文档 | `.pdf`（文字层，扫描页可选 OCR）、`.docx`、`.pptx`、`.xlsx`、`.xlsm` |
| 文本 | `.txt`、`.md`、`.markdown`、`.csv` |
| 图片 | `.png`、`.jpg`、`.jpeg`、`.heic`、`.heif`、`.tiff`、`.tif`、`.bmp`、`.webp`、`.gif`（走 OCR） |

- OCR 使用 **macOS 本地 Vision 框架**，免费、离线、无需密钥（`uv sync --extra ocr-macos`），
  也可以改用视觉大模型接口
- 旧版 Office 二进制格式（`.doc` / `.ppt` / `.xls`）会被明确拒绝并提示「另存为 .docx/.pptx/.xlsx」，
  而不是静默失败
- 每个文件的解析结果都会回报，一个坏文件不再拖垮整批上传

### 三、开发亮点

这个 fork 从头到尾做过一轮审计，并围绕「可验证的行为」重建，而不是「看起来能跑」：

| 亮点 | 说明 |
|---|---|
| **图谱去重** | 节点身份从「每次抽取生成新 `uuid4()`」改为 `(graph_id, name)`。反复重建会把每个实体重新插入一遍——实测有个库**9336 行节点实际只有 960 个不同名字**（光 `SAP` 就有 440 份副本）。附迁移脚本 `scripts/dedupe_graph.py` 修复历史库，重建也不再累积 |
| **可离线验证** | `pytest` 从 **0 个测试变成 133 个**，另有回测框架（桩驱动、39 项检查）、多格式摄入检查（11 例）、前端 Markdown 检查（18 例）。全部**不需要联网，也不消耗任何 LLM 调用** |
| **成本护栏** | 人设生成与模拟启动前都会先预估 LLM 调用次数，超出设置的预算上限时停下来等确认，而不是默默发出成千上万次调用 |
| **可复现的运行** | 每份报告都带 run manifest（提交号、输入、模型），报告内含情景树而不只是单一叙述 |
| **健壮的模拟生命周期** | 卡住的模拟会被检测出来，不再永远停在 "running"；后端重启遗留的孤儿子进程会在启动时回收；主动停止不再被误报成失败 |
| **抗重启的任务跟踪** | 长任务落盘保存，跨后端重启存活，界面不会永远转圈 |
| **安全默认值** | 后端默认只绑 `127.0.0.1`，CORS 只放行本地开发源 |
| **输入处理加固** | 上传的切块参数会被校验（退化的 `chunk_overlap` 曾能把后台线程送进无界循环）；报告正文渲染前会做 HTML 转义 |

### 四、冥想盆与水晶预言球 —— 记忆层与可验证的预言层

在原有五步预测流程之上，新增两个受《哈利·波特》神器启发的模块：

- **冥想盆（`/pensieve`）** —— 把项目、知识图谱、模拟与报告收割成记忆（`seed` / `world` /
  `entity` / `agent` / `episode` / `voice` / `prophecy` / `insight`），用血缘与关键词共振织成
  银色丝线；支持入盆回放、检索，以及把多条记忆蒸馏成新的洞察「记忆瓶」。报告与已结束的模拟
  会在后台自动收存（每个产物只收一次，不阻塞生成），回放中的每段时间线都带可点击的来源引用，
  能直接跳回对应的报告章节或模拟回合。存储：`backend/uploads/pensieve.db`。
- **水晶预言球（`/prophecy`）** —— 把一份已完成报告铸造成封存的预言球，包含预言正文、置信度、
  期限、情景分支与先兆指标；可以触碰开启、问卜，最后按 `fulfilled` / `partial` / `shattered`
  记录应验结果，并回流到冥想盆。已过期或 7 天内到期的球会出现在到期提醒横幅和导航角标里。
  存储：`backend/uploads/prophecy.db`。

两个模块都是增量式的：上传 → 图谱 → 模拟 → 报告的主流程不变。入口出现在首页导航、工作台页头和
报告页头。所有 LLM 调用在模型或 Key 不可用时都会降级为确定性的本地结果，接口不会因此失败。

### 五、报告直接读取模拟结果，并带可回溯引用

- **不再"看不见模拟"**：Report Agent 新增 `simulation_overview` / `simulation_search`
  两个工具，直接读取 `<sim>/<platform>/actions.jsonl` 与 `*_simulation.db`，
  不再依赖 `enable_graph_memory_update` 把动作回写图谱。
- **证据编号**：工具返回的每条帖子/评论/动作都带 `[S1]` 形式的编号；报告正文引用后，
  报告页会显示「证据与引用」面板，点击正文编号可跳回原文。
- **跑完仍可采访**：OASIS 进程已回收时，采访自动退回「人设 + 落盘行为」的回放模式，
  并明确标注为回放，而不是假装实时。
- **导出**：报告支持 Markdown、HTML（浏览器可直接打印成 PDF）与 DOCX。

### 六、质量、成本与可验证性加固

| 改进 | 说明 |
|---|---|
| 本体可编辑 | Step1 直接增删实体 / 关系类型；图谱已存在时提示需要重建 |
| 抽取提速 | 单 chunk 上限 3000 → 12000 字符；同批 chunk 并发抽取（默认 4 路）；SQLite 加 busy_timeout |
| 人设缓存 | 同一实体 + 上下文只生成一次，重建/重跑直接复用 |
| LLM 可靠性 | 超时 + 指数退避重试接入真实调用路径（此前 `retry.py` 没有任何引用） |
| 轮数护栏 | 默认 24h（24 轮），LLM 给出的时长会被夹到不超过 72 轮 |
| 成本预估 | 图谱未建时补算 chunk 阶段调用；报告章数、工具子调用仍按保守上界 |
| 记忆检索 | 冥想盆从 SQL `LIKE` 升级为 SQLite FTS5 trigram 全文索引（不可用时自动退回 LIKE） |
| 预言校准 | 预言球新增 Brier / log loss / 可靠性分桶，partial 不强行二值化 |

### 七、预测市场与同源分支（P1-3）

- **预测市场（派生层）**：每次模拟都会维护一个二元 LMSR 市场，问题是
  「模拟窗口内，以下结果是否会发生：<模拟需求>」。Twitter / Reddit 的帖子与评论
  按立场词变成 YES/NO 交易，价格、成交量、历史快照与逐笔成交都会落盘到
  `<sim>/market/market.db`。**不额外消耗 LLM 调用**；UI 会明确标注它由社群言论驱动，
  不是独立的 OASIS 交易平台。
  - API：`GET /api/simulation/<id>/market`
  - UI：模拟运行页顶部显示 YES/NO 价格、成交量、价格曲线与最近成交
  - 报告的大纲与证据概览也会带上市场状态
- **派生新闻台**：按互动量把高互动帖子/评论整理成头条
  （`GET /api/simulation/<id>/news`），模拟运行页与报告概览都会展示；
  它是可扫读的聚合视图，不是独立新闻平台。
- **同源分支 / A-B 对比**：从一个已有模拟克隆同一套配置与人设，创建新分支；
  可指定「第 N 轮注入事件」并在启动后自动注入。
  - `POST /api/simulation/<source_id>/fork`
  - `GET /api/simulation/<id>/forks` / `GET /api/simulation/<id>/fork`
  - `GET /api/simulation/<id>/compare?other_id=<other_id>`：对比动作数、分平台动作、
    最活跃 Agent、市场 YES 价格与成交量
  - UI：模拟运行页提供分支表单；分支页可一键与父模拟对比

> 分支是「同一初始条件 + 指定轮次注入差异事件」的重跑，不是 OASIS 中途状态快照。
> 这样可复现、无需改动 OASIS 内部状态；代价是分支会从第 0 轮重新消耗 LLM 调用。

### 八、实时通道与图谱编辑（P1-6 / P1-7）

- **报告日志 SSE**：`GET /api/report/<id>/agent-log/sse?from_line=N` 先回放已有日志，
  再每秒推送增量；完成/失败时发送 `event: done`。
- **模拟状态 SSE**：`GET /api/simulation/<id>/run-status/sse` 每秒推送 `run_state`。
- 前端报告页与模拟运行页优先使用 `EventSource`；网络或代理不支持时自动退回原来的轮询，
  功能不降级。控制台日志与动作明细仍保留轮询（数据量大，SSE 收益有限）。
- **图谱实体合并**：节点详情面板新增「合并到另一个实体」，选择 target 后确认。
  关系与属性并入 target，source 被删除，重复边自动去重。
  - API：`POST /api/graph/<graph_id>/merge`，body `{source_uuid, target_uuid}`
  - 合并不可撤销；UI 会明确提示。

## 🏗 运行逻辑

```
文档 ─► 本体 ─► 知识图谱 ─► 人设 ─► 社会模拟 ─► 报告 ─► 对话
       (LLM)   (本地 SQLite)  (LLM)   (OASIS)    (LLM)
```

| 阶段 | 做什么 |
|---|---|
| 1. 本体生成 | DeepSeek 阅读种子材料，给出实体类型与关系类型 |
| 2. 图谱构建 | 文档切块后抽取实体与关系，结果写入 SQLite |
| 3. 人设生成 | 图谱中的每个实体变成一个带人设的 Agent |
| 4. 社会模拟 | Agent 在模拟的 Twitter / Reddit 上自主互动，行为落盘为 posts / comments / actions；派生预测市场实时更新；可选回写图谱 |
| 5. 报告生成 | Report Agent 同时检索图谱与模拟落盘证据，撰写带 `[S#]` 引用的分析报告与情景树 |
| 6. 深度互动 | 与模拟世界中的任意个体对话，或与 Report Agent 对话 |

**进程模型：** Flask（端口 5001）提供 API，并把每一次模拟作为**独立子进程**托管；
前端是 Vite 应用（端口 3000）。运行中的事件注入与 Agent 访谈走基于文件的 IPC 通道
（`ipc_commands/` / `ipc_responses/`）。

**存储** —— 全部位于 `backend/uploads/`：

| 数据 | 位置 |
|---|---|
| 知识图谱 | `graphs.db`（SQLite） |
| 上传文件与项目 | `projects/` |
| 模拟运行 | `simulations/` |
| 报告 | `reports/` |
| 运行时设置（含你的 Key） | `macfish_settings.json`（已被 gitignore） |

## 🚀 快速开始

### 环境要求

| 工具 | 版本 | 检查 |
|---|---|---|
| **Node.js** | 18+ | `node -v` |
| **Python** | ≥3.11, ≤3.12 | `python --version` |
| **uv** | 最新版 | `uv --version` |

> 后端需要 Python ≥3.11。系统自带的 Python 太旧时，可以让 uv 代管：
> `uv python install 3.12`。

### 1. 配置环境变量

```bash
cp .env.example .env
# 然后编辑 .env，填入你的 DeepSeek Key
```

```env
LLM_PROVIDER=network
LLM_API_KEY=sk-your_deepseek_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat
```

> **不需要 Zep Cloud Key**，知识图谱是本地 SQLite。任何 OpenAI 兼容的供应商都可以，
> 改 `LLM_BASE_URL` / `LLM_MODEL_NAME` 指过去即可。

### 2. 安装依赖

```bash
npm run setup:all
```

也可以分步：`npm run setup`（Node）与 `npm run setup:backend`（Python）。

可选：图片与扫描版 PDF 的本地 OCR（仅 macOS）：

```bash
cd backend && uv sync --extra ocr-macos
```

### 3. 启动

```bash
npm run dev
```

- 前端：<http://localhost:3000>
- 后端 API：<http://localhost:5001>

单独启动：`npm run backend` / `npm run frontend`。

### Docker

```bash
cp .env.example .env
docker compose up -d
```

## 📖 使用说明

1. **创建项目** —— 打开 <http://localhost:3000>，上传种子文档（格式见上表）或粘贴文本，
   并描述你的预测需求。
2. **生成本体** —— DeepSeek 给出实体类型与关系类型，确认前可以自行调整。
3. **构建知识图谱** —— 文档切块后抽取进 `graphs.db`。重建会替换掉现有图谱，所以会先问一次。
4. **运行模拟** —— 由图谱实体生成人设，Agent 在模拟平台上自主互动，运行中可以注入事件。
5. **生成报告** —— Report Agent 同时检索图谱与模拟落盘证据，撰写带 `[S#]` 引用和情景树的报告，
   支持导出 Markdown / HTML / DOCX；之后可与任意 Agent 或 Report Agent 对话。

### 常见问题

| 现象 | 处理 |
|---|---|
| DeepSeek 额度用尽 | 把 `LLM_BASE_URL` / `LLM_MODEL_NAME` 指到别的 OpenAI 兼容供应商 |
| 抽取质量不理想 | 在本体生成阶段调整实体 / 关系类型 |
| 模拟太慢 | 调小 `.env` 里的 `OASIS_DEFAULT_TOTAL_HOURS` 或 `OASIS_MAX_ROUNDS`（默认 24h / 72 轮上限） |
| 模拟卡在 "running" | 运行器现在会检测停滞，并在启动时回收孤儿进程 |
| 实体数看着被封顶了 | 超过读取上限的图谱会被截断，界面现在会明确提示 |
| 想重置图谱 | 用「删除并重建」，或直接删掉 `backend/uploads/graphs.db` |

## 🧪 测试

```bash
cd backend
uv run pytest tests -q                              # 133 项，离线
uv run python scripts/run_benchmark.py verify       # 39 项检查，桩驱动
uv run python scripts/test_multi_format.py          # 11 例格式
```

```bash
cd frontend
node scripts/check-markdown.mjs                     # 18 例
```

以上全部不需要联网，也不会消耗 LLM 调用。

## 📄 引用

Forked from **[MiroFish](https://github.com/666ghj/MiroFish)**。

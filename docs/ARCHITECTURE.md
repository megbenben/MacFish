# MacFish 架构与运行逻辑

> 本文梳理 MacFish（内部包名仍为 `mirofish`）的完整运行逻辑：一次预测从上传种子材料到产出报告，
> 数据在哪些模块之间流转、落在磁盘的什么位置、由哪个进程执行。
>
> 代码位置：仓库根 `~/MacFish`；后端包 `backend/app`；前端 `frontend/src`。

---

## 1. 一句话概括

MacFish 是一个**多智能体预测引擎**：把现实世界的种子材料（新闻、报告、小说）交给 LLM 抽取出实体与关系，
构建一张知识图谱；再由图谱中的实体生成成千上万个带人设的 Agent，让它们在模拟的 Twitter / Reddit
上自主互动演化；最后由 Report Agent 与模拟后的环境交互，产出一份预测报告。

```
种子文档 ──► 本体 ──► 知识图谱 ──► Agent 人设 ──► 模拟配置 ──► 社会模拟 ──► 预测报告 ──► 深度互动
 上传        LLM       LLM 抽取       LLM 生成        LLM 生成      子进程        ReportAgent    对话
```

---

## 2. 六阶段详解

### 阶段一：本体生成（Ontology）

| | |
|---|---|
| **入口** | `POST /api/graph/ontology/generate`（multipart）— `backend/app/api/graph.py` |
| **输入** | 若干个文件（`files`）+ 自然语言的模拟需求（`simulation_requirement`） |
| **处理** | ① 逐文件解析成文本（`FileParser`，见 §4）→ ② `TextProcessor.preprocess_text()` 规整 → ③ `OntologyGenerator.generate()` 交给 LLM |
| **输出** | 10 个实体类型 + 关系类型定义 + `analysis_summary` |
| **落盘** | `backend/uploads/projects/{project_id}/` 下的 `project.json`、`files/`、`extracted_text.txt` |

要点：

- 本体固定为 **10 个实体类型**，最后两个强制是兜底类型 `Person` 与 `Organization`
  （`ontology_generator.py` 的 `_validate_and_process()`，不足时会从尾部替换补齐）。
- 实体名强制 PascalCase、关系名强制 UPPER_SNAKE_CASE，便于后续按类型过滤 Agent。
- 传给 LLM 的文本总量上限 **50000 字符**，按文档比例分配配额（`_allocate_documents()`），
  每篇保底 4000 字符，截断点回退到块边界以免把表格切成半截。
- `project.status` 流转：`created` → `ontology_generated`。

### 阶段二：知识图谱构建

| | |
|---|---|
| **入口** | `POST /api/graph/build` → 起 **daemon 线程**，立即返回 `task_id` |
| **处理** | `GraphBuilderService`：`create_graph()` → `set_ontology()` → `add_text_batches()` → `_wait_for_episodes()` |
| **输出** | `graph_id`（形如 `mirofish_<16位hex>`）+ 节点/边 |
| **落盘** | `backend/uploads/graphs.db`（SQLite，WAL 模式） |

数据流的关键一环：**图谱内容是 LLM 抽出来的，不是规则抽的**。

```
文本 → split_text_into_chunks(chunk_size=500, overlap=50)
     → 每 3 块打包成一个 episode
     → LocalGraphClient.graph.add_batch()
         └─ DeepSeekGraphExtractor.extract_from_text()
              └─ LLM 返回 {"entities":[...], "relations":[...]}
     → upsert 进 SQLite 的 nodes / edges 表
```

- **没有 Zep Cloud**。`LocalGraphStore`（`services/local_graph_store.py`）是 SQLite 实现，
  `LocalGraphClient`（`utils/local_graph_client.py`）是它的 Zep 兼容外壳。
  所有 `zep_*` 命名都是历史遗留，运行时与 Zep 无关。
- episode 文本会在抽取前被截断到 3000 字符（`DeepSeekGraphExtractor._build_extraction_prompt`）。
- 每个源文档的失败/成功都会写进日志；`project.graph_id` 与 `status=graph_completed` 在完成后落盘。
- 前端轮询 `GET /api/graph/task/{task_id}` 拿进度（进度值由批次回调上报）。

### 阶段三：Agent 人设生成

| | |
|---|---|
| **入口** | `POST /api/simulation/create` 建模拟，再 `POST /api/simulation/prepare`（起后台线程） |
| **处理** | `ZepEntityReader.filter_defined_entities()` 取实体 → `OasisProfileGenerator.generate_profiles_from_entities()` |
| **输出** | 每个实体一份人设（姓名、简介、MBTI、职业、立场、影响力权重…） |
| **落盘** | `backend/uploads/simulations/{sim_id}/reddit_profiles.json`、`twitter_profiles.csv` |

要点：

- 只有带**自定义标签**（非 `Entity` / `Node`）的节点才会变成 Agent；
  若调用方指定了 `entity_types`，则还要与本体类型求交集。
- 人设走 **ThreadPoolExecutor 并行**（默认 5 并发），每生成一个就实时落盘
  （`save_profiles_realtime`），所以前端能看到人设"逐个长出来"。
- LLM 生成失败时回退到规则生成（`_generate_profile_rule_based`）。
- 人设会额外从图谱里检索该实体的关联边/节点作为上下文（`_search_zep_for_entity`）。
- 两种格式并存是硬要求：Reddit 侧读 JSON，Twitter 侧读 CSV（列 `user_id,name,username,user_char,description`）。

### 阶段四：模拟配置生成

| | |
|---|---|
| **入口** | 阶段三的同一条 `prepare` 流程的后半段 |
| **处理** | `SimulationConfigGenerator.generate_config()`，分阶段调用 LLM |
| **输出** | 时间流速、事件、每个 Agent 的活跃度、平台推荐权重 |
| **落盘** | `backend/uploads/simulations/{sim_id}/simulation_config.json` |

分阶段顺序（每段单独一次 LLM 调用，避免一次生成过大 JSON）：

1. **时间配置** `time_config`：总模拟小时数、每轮代表几分钟、高峰/低谷/工作时段的活动倍率
2. **事件配置** `event_config`：`initial_posts`（开局帖子）、`scheduled_events`、`hot_topics`
3. **Agent 配置** `agent_configs`：每批 15 个，含 `activity_level`、`posts_per_hour`、`stance`、`influence_weight`
4. **平台配置** `twitter_config` / `reddit_config`：`recency_weight`、`popularity_weight`、`echo_chamber_strength`
5. `_assign_initial_post_agents()`：把开局帖子的 `poster_type` 映射到具体 Agent

`total_rounds = total_simulation_hours * 60 / minutes_per_round`。完成后 `status = ready`。

### 阶段五：社会模拟（OASIS）

| | |
|---|---|
| **入口** | `POST /api/simulation/start`（`platform` = `twitter` / `reddit` / `parallel`） |
| **执行者** | **独立子进程**：`backend/scripts/run_{twitter,reddit,parallel}_simulation.py` |
| **输出** | 每轮每个 Agent 的动作流 |
| **落盘** | `{sim_id}/twitter_simulation.db`、`reddit_simulation.db`、`{twitter,reddit}/actions.jsonl`、`simulation.log`、`run_state.json` |

启动方式（`services/simulation_runner.py`）：

```python
cmd = [sys.executable, SCRIPTS_DIR / script, "--config", sim_dir / "simulation_config.json"]
if max_rounds: cmd += ["--max-rounds", str(max_rounds)]
subprocess.Popen(cmd, cwd=sim_dir, stdout=simulation.log, start_new_session=True)
```

`start_new_session=True` 让子进程自成进程组，便于整组终止（`os.killpg`）。

脚本内部：`oasis.make(agent_graph, platform=..., database_path=...)` → `env.reset()` →
每轮选出活跃 Agent 并发起 LLM 动作 → 从 SQLite 的 `trace` 表读回新动作 →
经 `_enrich_action_context()` 补上下文 → 追加写入 `{platform}/actions.jsonl`。

Flask 侧有一个**监控线程**持续 tail 这两个 JSONL：

- 见到 `round_end` → 更新该平台轮数/模拟小时数
- 见到 `simulation_end` → 标记该平台完成
- 其他 → 转成 `AgentAction` 写进内存队列，并在开启时喂给 `ZepGraphMemoryUpdater`
  （每 5 条攒一批写回图谱，形成"时序记忆"）
- 两个平台都完成或进程退出 → 状态改为 `completed`（返回码非 0 则 `failed`，并截取日志尾部 2000 字符作为错误）

模拟结束后子进程**不退出**，而是进入等待命令循环（见 §5 IPC），这样才能被"采访"。

### 阶段六：报告生成

| | |
|---|---|
| **入口** | `POST /api/report/generate`（起后台线程） |
| **执行者** | `services/report_agent.py` 的 `ReportAgent` |
| **输出** | 大纲 + 逐章正文 |
| **落盘** | `backend/uploads/reports/{report_id}/` |

流程：`plan_outline()`（一次 LLM 生成章节大纲）→ 逐章 `_generate_section_react()`：

```
每章是一个 ReACT 循环（最少 3 次、最多 5 次工具调用）
  工具集：insight_forge（子问题拆解 + 深挖）
          panorama_search（全景检索，含已失效的历史事实）
          quick_search（快速检索）
          interview_agents（直接采访模拟中的 Agent 并汇总观点）
  达到上限则强制收尾出答案
```

产物文件：`meta.json`、`outline.json`、`progress.json`、`section_NN.md`、`full_report.md`、
`agent_log.jsonl`、`console_log.txt`。

**前端拿报告内容的方式比较特别**：不是请求一个"报告正文"接口，而是每 2 秒增量拉
`GET /api/report/{id}/agent-log?from_line=N`，从日志事件里拼装：

| 日志 action | 前端动作 |
|---|---|
| `planning_complete` | 设置 `reportOutline` |
| `section_start` | 标记当前章节 |
| `section_complete` | 把 `details.content` 填进 `generatedSections[i]` |
| `report_complete` | 标记完成并停止轮询 |

### 阶段七：深度互动

| 接口 | 作用 |
|---|---|
| `POST /api/report/chat` | 与 Report Agent 对话（带工具调用，返回 `response` / `tool_calls` / `sources`）|
| `POST /api/simulation/interview/batch` | 采访模拟世界里的任意 Agent，返回该 Agent 的答复 |

Agent 采访必须经 IPC 转发给还活着的模拟子进程（§5）。

---

## 3. 进程与线程模型

```
┌─ Flask 主进程（:5001，threaded=True）────────────────────────────┐
│                                                                  │
│  ├─ 请求线程：所有同步 API                                        │
│  │                                                               │
│  ├─ daemon 线程：图谱构建（长任务，立即返回 task_id）              │
│  ├─ daemon 线程：模拟准备 /prepare（人设 + 配置）                  │
│  ├─ daemon 线程：报告生成 /report/generate                        │
│  │                                                               │
│  └─ 监控线程：tail actions.jsonl（每个模拟一个）                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                     │ Popen(start_new_session=True)
                     ▼
┌─ 模拟子进程：run_parallel_simulation.py ──────────────────────────┐
│  主循环跑 OASIS 轮次 → 写入 {platform}/actions.jsonl 和 *.db       │
│  跑完后进入 IPC 等待循环（可被采访 / 关闭）                        │
└──────────────────────────────────────────────────────────────────┘
```

### 两个容易踩的坑

**① locale 是线程局域的。** `utils/locale.py` 用 `threading.local()` 存语言；而
`get_locale()` 在**没有请求上下文**时读这个局域变量。所以每个后台线程都必须
**在 spawn 之前捕获** `get_locale()`，进线程后立刻 `set_locale(...)`，否则线程内
所有提示语都会退回默认的 `zh`。代码里已经这样做了（`graph.py`、`report.py`、
`simulation.py`、`simulation_runner.py` 都有对应写法），新增后台线程时务必照做。

**② 国际化文案有两套读取方式，但只有一份数据。** 后端 `t('api.xxx')` 与前端 `$t('api.xxx')`
读的是同一批文件 `locales/*.json`（`locale.py` 从仓库根加载）。所以**新增文案必须同时改
`locales/zh.json` 和 `locales/en.json`**——只加中文会让英文界面显示中文，反之亦然。
另外 `t()` 遇到拼错的键会**原样返回键名**，拼错时用户会直接看到 `api.someKey`。

---

## 4. 文件解析链路

这是唯一一处"非 LLM"的输入处理，也是多格式支持的落点。

```
上传文件
  └─ graph.py 校验 classify() ──► supported / legacy / unsupported
        │                              │            │
        │                              │            └─► 明确报错（不再静默丢弃）
        │                              └─► 旧版 Office：提示「另存为 .docx/.pptx/.xlsx」
        ▼
   ProjectManager.save_file_to_project()   → projects/{id}/files/{8位hex}.{ext}
        ▼
   FileParser.extract_text_detailed()      → utils/parsers/ 按扩展名分派
        ▼
   TextProcessor.preprocess_text()         → 规整换行、逐行 strip
        ▼
   OntologyGenerator._allocate_documents() → 按文档比例分配 5 万字配额
```

| 扩展名 | 解析器 | 说明 |
|---|---|---|
| `.txt` `.md` `.markdown` | `parsers/text_parser.py` | 编码探测：UTF-8 → charset_normalizer → chardet → replace |
| `.csv` | 同上 | `csv.Sniffer` 猜分隔符，输出 Markdown 表格 |
| `.pdf` | `parsers/pdf_parser.py` | 逐页取文字层；纯扫描页走 OCR（按页判定） |
| `.docx` | `parsers/office_parser.py` | 按 XML 文档顺序输出标题/段落/表格，末尾汇总文本框与页眉页脚 |
| `.pptx` | 同上 | 每页一个章节，递归展开分组形状，含表格/图表/演讲者备注 |
| `.xlsx` `.xlsm` | 同上 | 每表一个章节，`read_only` 流式读取，行列有上限 |
| 图片 | `parsers/image_parser.py` | 走 OCR（见 §5） |
| `.doc` `.ppt` `.xls` | — | 不解析，返回转换提示 |

**输出格式契约**（所有解析器共同遵守，原因见下）：

1. 结构边界一律用 ATX 标题
2. 绝不产生连续 3 个以上换行
3. 表格内部绝不出现空行
4. 绝不依赖行首缩进，嵌套深度 ≤ 1

为什么：`TextProcessor.preprocess_text()` 会做 `re.sub(r'\n{3,}', '\n\n', text)` 并对每一行
`strip()`。它会吃掉行首缩进、压掉多余空行，所以解析器的输出被刻意设计成"对预处理免疫"——
表格和标题能原样穿过，嵌套列表则统一压成单层（子项用 `- （1）…` 前缀表达层级）。

---

## 5. OCR 与文件 IPC

### OCR 引擎

`utils/ocr.py` 提供统一接口 `recognize(bytes) -> OCRResult`，两个引擎：

| 引擎 | 依赖 | 特点 |
|---|---|---|
| `MacVisionOCREngine` | `pyobjc-framework-Vision` / `-Quartz`（extra `ocr-macos`） | macOS 原生 Vision，免费、离线、无需密钥；只识别文字，不解读图表 |
| `ApiVisionOCREngine` | 已声明的 `openai` SDK | OpenAI 兼容视觉模型（默认阿里百炼 `qwen-vl-max`），能理解图表语义，需单独密钥 |

选择顺序由 `ocr.engine` 决定：

- `auto`（默认）：本地 Vision → 视觉 API
- `vision`：只用本地；不可用时若配了 API 密钥则降级并记录说明
- `api`：只用视觉 API，**不会**静默改用本地
- `off`：不用 OCR。图片会得到明确报错（"需启用 OCR"），而不是被静默跳过

接口收 **bytes 而不是路径**：同一套接口要服务独立图片、PDF 渲染页、以及将来的 pptx 内嵌图，
谁都不必落临时文件。`CGImageSourceCreateWithData` 直接吃 bytes，原生支持 PNG/JPEG/TIFF/BMP/GIF/HEIC。

两个实现细节值得记住：**每次调用都要新建 `VNRecognizeTextRequest`**（Vision 的 request
不可跨线程复用，而 Flask 是 `threaded=True`）；`ApiVisionOCREngine` **必须用显式参数构造
`LLMClient`**，因为 `RuntimeSettings.get_llm_config()` 对未知 provider 会静默回退到
`network` 配置——写成 `provider='vision'` 会把图片发给 `deepseek-chat`。

### 扫描版 PDF 的判定

逐页判定（不按文档）：`page.get_text()` 少于 20 字符即认为是扫描页。
阈值刻意定得低——扫描页通常只有 0~3 个杂散字形，而"页面稀疏但有文字"（标题页、只有图注的页）
不该被误判。判定命中且 OCR 可用时，按 200 dpi 渲染成 PNG 再识别，单份文档上限 30 页。
未启用 OCR 时，扫描页会输出**可见**的说明行，而不是留空。

### 文件 IPC（Flask ↔ 模拟子进程）

模拟子进程跑完后不退出，而是轮询一个目录里的命令文件，从而实现"采访还活着的 Agent"。

```
simulations/{sim_id}/
├── ipc_commands/     Flask 写命令，子进程读
├── ipc_responses/    子进程写结果，Flask 读
└── env_status.json   子进程写 {"status": "alive"|"running"|"stopped", ...}
```

| 命令 | 参数 |
|---|---|
| `interview` | `{agent_id, prompt, platform?}` |
| `batch_interview` | `{interviews: [{agent_id, prompt, platform?}], platform?}` |
| `close_env` | `{}` |

协议：Flask 写 `ipc_commands/{uuid}.json` → 子进程轮询（每 0.5s）执行 → 写
`ipc_responses/{uuid}.json` → Flask 轮询取走并删除两侧文件。超时抛 `TimeoutError`。
子进程侧的采访实现是发一个 `ManualAction(action_type=INTERVIEW)`，再从 SQLite `trace`
表的 `info` 列读回结果。

---

## 6. 存储地图

`backend/uploads/` 下的一切：

| 路径 | 内容 |
|---|---|
| `graphs.db`（+`-wal` `-shm`） | SQLite：`graphs` / `nodes` / `edges` / `episodes` 四张表 |
| `projects/{proj_id}/project.json` | 项目元信息（状态、本体、graph_id、模拟需求） |
| `projects/{proj_id}/files/{8hex}.{ext}` | 上传的原始文件（保存时重命名，扩展名保留） |
| `projects/{proj_id}/extracted_text.txt` | 所有文档提取并规整后的合并文本 |
| `simulations/{sim_id}/state.json` | 模拟状态（阶段、实体数、人设数） |
| `simulations/{sim_id}/run_state.json` | 运行状态（轮数、小时数、各平台进度、动作数） |
| `simulations/{sim_id}/simulation_config.json` | 完整模拟配置 |
| `simulations/{sim_id}/reddit_profiles.json` | Reddit 侧人设 |
| `simulations/{sim_id}/twitter_profiles.csv` | Twitter 侧人设 |
| `simulations/{sim_id}/{twitter,reddit}_simulation.db` | OASIS 的 SQLite（`user`/`post`/`comment`/`follow`/`trace`） |
| `simulations/{sim_id}/{twitter,reddit}/actions.jsonl` | 动作日志（监控线程 tail 的文件） |
| `simulations/{sim_id}/simulation.log` | 子进程 stdout/stderr 合并 |
| `simulations/{sim_id}/env_status.json` + `ipc_*` | IPC |
| `reports/{report_id}/` | `meta.json` `outline.json` `progress.json` `section_NN.md` `full_report.md` `agent_log.jsonl` `console_log.txt` |
| `../macfish_settings.json`（仓库根） | 运行时设置（LLM / OCR），优先级**高于** `.env` |
| `backend/logs/YYYY-MM-DD.log` | 后端日志，10MB × 5 轮转 |

---

## 7. 前端流程

路由与 5 个步骤的对应关系（注意 `views/Process.vue` 是**未被路由引用的死代码**，
真正生效的是 `views/MainView.vue`）：

| 路由 | 视图 | 步骤 |
|---|---|---|
| `/` | `Home.vue` | 上传 + 历史记录 |
| `/process/:projectId` | `MainView.vue` | Step1 图谱构建（含 Step2 嵌入） |
| `/simulation/:simulationId` | `SimulationView.vue` | Step2 环境搭建 |
| `/simulation/:simulationId/start` | `SimulationRunView.vue` | Step3 开始模拟 |
| `/report/:reportId` | `ReportView.vue` | Step4 报告生成 |
| `/interaction/:reportId` | `InteractionView.vue` | Step5 深度互动 |

**全程没有 SSE / WebSocket**，是 8 处 `setInterval` HTTP 轮询：

| 位置 | 轮询对象 | 间隔 |
|---|---|---|
| `MainView` | 任务状态 / 图谱数据 | 2s / 10s |
| `Step2EnvSetup` | 准备状态 / 人设 / 配置 | 2s / 3s / 2s |
| `Step3Simulation` | 运行状态 / 明细 | 2s / 3s |
| `Step4Report` | agent-log / console-log | 2s / 1.5s |

Axios 实例（`api/index.js`）统一带 `Accept-Language` 头（后端据此决定 LLM 输出语言）、
5 分钟超时、以及"`success: false` 即 reject"的响应拦截。写操作走
`requestWithRetry`（指数退避），但 **4xx 不重试**——上传这类请求重试会把整个 body 重传。

---

## 8. API 一览

### `/api/graph`

| 方法 | 路径 | 关键入参 | 说明 |
|---|---|---|---|
| POST | `/ontology/generate` | `files[]` `simulation_requirement` | 上传并生成本体；返回 `file_results` 逐文件结果 |
| POST | `/build` | `project_id` `chunk_size` `force` | 起后台线程构建图谱，返回 `task_id` |
| GET | `/task/{task_id}` | — | 构建进度（内存态，重启即失效） |
| GET | `/data/{graph_id}` | — | 节点与边 |
| GET | `/project/{project_id}` | — | 项目详情 |
| GET | `/project/list` | `limit` | 项目列表 |
| POST | `/project/{id}/reset` | — | 重置到本体已生成状态 |
| DELETE | `/project/{id}` | — | 删除项目（含文件） |
| DELETE | `/delete/{graph_id}` | — | 删除图谱 |

### `/api/simulation`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/create` | 建模拟（绑 project + graph） |
| POST | `/prepare` | 后台生成人设 + 配置 |
| POST | `/prepare/status` | 准备进度 |
| POST | `/start` | 启动模拟子进程（`platform`/`max_rounds`/`force`） |
| POST | `/stop` | 停止（整进程组） |
| GET | `/{id}/run-status` `/run-status/detail` | 运行状态 / 含动作明细 |
| GET | `/{id}/profiles` `/profiles/realtime` | 人设（后者直读文件，支持"正在生成"） |
| GET | `/{id}/config` `/config/realtime` | 模拟配置 |
| GET | `/{id}/actions` `/timeline` `/agent-stats` | 动作 / 时间线 / Agent 统计 |
| GET | `/{id}/posts` `/comments` | 直读 OASIS SQLite |
| POST | `/interview/batch` `/interview/all` | 采访 Agent |
| POST | `/env-status` `/close-env` | IPC 存活探测 / 关闭环境 |
| GET | `/entities/{graph_id}` | 按本体类型过滤实体 |
| GET | `/list` `/history` | 列表 / 历史（含 report_id 与文件名） |

### `/api/report`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/generate` | 后台生成报告 |
| GET | `/{id}` | 报告全文与元信息 |
| GET | `/{id}/agent-log?from_line=N` | 增量日志（前端据此拼报告） |
| GET | `/{id}/sections` `/section/{i}` `/progress` | 分节读取 |
| GET | `/{id}/download` | 下载 Markdown |
| POST | `/chat` | 与 Report Agent 对话 |
| POST | `/tools/search` `/tools/statistics` | 图谱检索工具直调 |
| GET | `/by-simulation/{sim_id}` `/check/{sim_id}` | 按模拟查报告 |

### `/api/settings`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/llm` | 读取/更新 LLM 供应商配置 |
| POST | `/llm/test` | 连通性测试 |
| GET/PUT | `/ocr` | 读取/更新 OCR 配置（返回引擎可用性） |
| POST | `/ocr/test` | 内存渲染测试图并跑一遍 OCR |

---

## 9. 状态机与持久化边界

```
Project:    created ──► ontology_generated ──► graph_building ──► graph_completed
                                                              └► failed

Simulation: created ──► preparing ──► ready ──► running ──► completed
                                        └────► paused / stopped / failed

Task(内存): pending ──► processing ──► completed / failed
```

**最重要的一条边界：`TaskManager` 是纯内存的**（`models/task.py`，只是个 dict + 锁）。
后端一重启，所有 `task_id` 全部失效。为此有两处"自愈"补偿：
`/api/simulation/prepare/status` 与 `/api/report/generate/status` 会去扫 `state.json` /
产物文件重新推断状态。但 `GET /api/graph/task/{task_id}` **没有**这类补偿——
如果构建图谱期间重启了后端，前端会一直转圈，需要重新触发一次构建。

其它持久化事实：

- 运行时设置 `macfish_settings.json`（仓库根）优先级**高于** `.env`：
  `RuntimeSettings` 先读 `.env` 得到默认值，若存在 JSON 文件则完全以它为准。
  但 `_load()` 是裸 `json.load`、**不与默认值合并**，所以新增设置项必须在读取时
  自己做深合并（`get_ocr_config()` 就是这么做的），否则老配置文件会让新项变成 `None`。
- `.env` 由 `config.py` 以 `override=True` 加载，路径是**仓库根**的 `.env`（不是 `backend/.env`）。
- `Config.validate()` 现在只校验 `LLM_API_KEY`（且仅在 `LLM_PROVIDER=network` 时）。
  历史版本曾**无条件**要求 `ZEP_API_KEY`，而 `run.py` 遇到任何校验错误直接 `sys.exit(1)`，
  所以那时删掉这个键会让后端起不来——该要求已移除，`Config.ZEP_API_KEY` 仅作为
  兼容旧调用点的遗留属性保留（`LocalGraphClient` 不使用 api_key）。
- 后端默认只监听 `127.0.0.1`（`FLASK_HOST` 可覆盖，设 `0.0.0.0` 会有启动警告），
  CORS 默认只放行 `http://localhost:3000` 与 `http://127.0.0.1:3000`（`CORS_ORIGINS` 可覆盖）。

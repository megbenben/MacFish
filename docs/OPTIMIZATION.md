# MacFish 优化建议清单

> 每条给出：**现象** → **证据**（文件:行号）→ **建议** → **代价**。
> 标注 ✅ 的表示已修，条目末尾会附"已完成"说明。已在两轮里销掉：
> 1.2 / 1.3 / 1.4 / 2.3（2026-10-06 多格式上传），**1.1 / 1.8 / 4.1**（2026-10-07）。
> 其余为待办。
>
> 优先级判断标准：★ 影响正确性/安全性，★★ 影响成本或体验，★★★ 纯可维护性。

---

## 0. 第三轮（2026-10-07 晚）：外部审查 + 22 项修复

在本文档之上另开两路独立的只读代码审查（后端 / 前端），**新查出 17 条本文档未覆盖的真问题**
（下表 B* 为后端审查、F* 为前端审查），连同原有条目一并打分后，本轮实施 22 项。
下轮清单见本文档末尾"附录：余下待办"。

本轮修掉的（编号沿用本文档原有条目号，新增问题用 B/F 前缀）：

| 编号 | 问题 | 状态 |
|---|---|---|
| B1 | `chunk_size`/`chunk_overlap` 不校验 → `start = end - overlap` 原地打转，后台线程死循环到 OOM | ✅ 已修 |
| B2 | `/stop` 写 `paused` 而就绪白名单没有它 → **停止后再也 `/start` 不了**（force 也走不到） | ✅ 已修 |
| B3 | 停止竞态：睡醒的监视线程拿退出码 `-15` 把 `STOPPED` 改写回 `FAILED` | ✅ 已修 |
| B4 | 单平台模拟永远过不了就绪检查（`required_files` 硬编码两个平台） | ✅ 已修 |
| B5 | `LLMClient.chat` 遇空 content 抛 `TypeError` 让整份报告作废；`report_agent` 的 `is None` 兜底是死代码 | ✅ 已修 |
| B6 | `_read_action_log` 解析失败仍 `f.tell()` 越过半行 → 动作永久丢失且无日志 | ✅ 已修 |
| F1 | 前端完成判据漏 `failed`/`stalled` → 失败时轮询永不停止，页面永远「运行中/生成中」 | ✅ 已修 |
| F2 | Step3 写死 `force: true` → 后端预算护栏从 UI 走的路永不触发；预估与启动并发 | ✅ 已修 |
| F4 | Home 的「不支持的文件」提示只写不读，从未渲染（1.2 的整改其实没生效） | ✅ 已修 |
| F5 | 文件选择框不重置 → 移除后重选同一文件无反应 | ✅ 已修 |
| F6 | 前端写死绝对 `baseURL` → `vite.config.js` 的 `/api` 代理是死配置，每请求跨域 | ✅ 已修 |
| F7 | 从首页历史进 Step 2 会静默杀掉正在跑的模拟 | ✅ 已修 |
| F8 | `Step5Interaction` 缺相等性守卫 + `onMounted` 重复调用 → 进页面发两遍全量请求 | ✅ 已修 |
| F9 | `MainView` 的 Step2 接线是死路径，且缺 `>= 3` 分支（改一下就是空白页） | ✅ 已修（删除死接线） |
| F10 | `Step5Interaction` 未声明 `systemLogs` prop，落到 DOM 上 | ✅ 已修 |
| 1.5 | `TaskManager` 重启后 task_id 失效、图谱任务无补偿 | ⏳ 下轮 |
| 1.6 | `fetch_all_edges` 没有数量上限 | ✅ 已修 |
| 1.7 | `generate_python_code()` 必然 `ImportError`（零调用方） | ✅ 已删除 |
| 1.8 遗留 | 构建前询问「累积 or 重建」+ 2000 上限界面提示 | ⏳ 下轮（另发现 `force` 重建时旧图谱只置空 `graph_id` 不删行，存在**孤儿图谱泄漏**，要一并设计） |
| 2.5 | debug 重载后的孤儿子进程无人回收 | ✅ 已修（启动时扫 `run_state.json` 回收） |
| 2.6 | 模拟跑完不收敛到 completed | ✅ 已修（新增空闲兜底 + `stalled` 状态） |
| 3.1 | `views/Process.vue` 2070 行死代码 | ✅ 已删 |
| 3.3 | 两份 `renderMarkdown` + 无转义 | ⏳ 下轮 |
| 3.4 | `languages.json` 声明 7 种语言但只有 2 个文件 | ✅ 已对齐（并对不上时改为告警） |
| 3.5 | 死导出 + 项目没有自动化测试 | ✅ 部分：删掉死导出；新增 `tests/test_pure_utils.py`、`tests/test_runner_fixes.py`（测试总数 40 → 69） |
| A2 | 模拟自然跑完不收敛（见 2.6） | ✅ 已修 |
| A11 | 两份 `renderMarkdown` 合并 + 转义 | ⏳ 下轮 |
| B7 | `ReportConsoleLogger` 把 FileHandler 挂到全局 logger → 并发报告日志互相污染 | ⏳ 下轮 |
| F3 | `GraphPanel` 每次数据变化整图重建，缩放/选中丢失 | ⏳ 下轮 |
| 2.1 / 2.2 / 2.4 / 3.2 / 2.7 / 4.2 / 4.3 | SSE、抽取节奏、异步上传、拆巨型组件、长任务阻塞诊断、上传进度、XML 加固 | ⏳ 下轮 |

验证（全部离线、零 LLM 花费）：`uv run pytest tests -q` **69 passed**；
`uv run python scripts/run_benchmark.py verify` **39/39**；
`uv run python scripts/test_multi_format.py` **11/11**；`npm run build` 通过。


---

## 一、正确性

### 1.1 ✅ `ZEP_API_KEY` 启动校验是个死结（2026-10-07 已修）

**现象**：知识图谱早已本地化（SQLite，与 Zep Cloud 无关），但 `Config.validate()` 仍然
**无条件**要求这个键存在；而 `run.py` 遇到任何校验错误就直接退出。结果是——从 `.env`
里删掉这个已经没用的键，会让**所有全新安装都起不来**。

**证据**：`backend/app/config.py:90-91`（无条件 append 错误）、`backend/run.py:28,34`
（`sys.exit(1)`）。此外 `ZepEntityReader`、`ZepToolsService`、`ZepGraphMemoryUpdater`
三个类的构造函数也各自判断了这个键。

**建议**：一次改全，否则删一半更糟：
1. `config.py` 的 `validate()` 去掉 ZEP 检查
2. `.env.example` 移除 `ZEP_API_KEY`
3. `zep_entity_reader.py` / `zep_tools.py` / `zep_graph_memory_updater.py` 里的
   `if not ZEP_API_KEY: raise` 一并去掉

**代价**：小（约 5 处删除）。风险是这三处的判断可能被别处依赖，删前 grep 一遍。

**已完成（2026-10-07）**：实际比预估的"5 处"更多——grep 出 **10 处**关卡，全部清掉：

- `config.py` `validate()` 的 ZEP 检查（保留 `Config.ZEP_API_KEY` 属性本身，
  因为 4 处旧调用仍写 `api_key or Config.ZEP_API_KEY`；`LocalGraphClient.__init__`
  的签名是 `(api_key="")` 且**完全不使用**它）
- `.env.example` 的 ZEP 段（改为一句"本项目不需要 ZEP_API_KEY"）
- 服务层 3 处 `raise ValueError("ZEP_API_KEY 未配置")`：`zep_entity_reader.py`、
  `zep_tools.py`、`zep_graph_memory_updater.py`
- API 层 5 处硬拦截（`if not Config.ZEP_API_KEY: return 500`）：
  `api/simulation.py` ×3、`api/graph.py` ×2
- `oasis_profile_generator.py` 顺带修正：原先 `if self.zep_api_key:` 才建本地图谱客户端，
  一旦键为空就会**静默降级**成"没有图谱上下文"；已改为无条件初始化

验证：`Config.validate()` 在 `ZEP_API_KEY` 缺失时返回 `[]`，`create_app()` 正常（77 条路由）。

---

### 1.2 ✅ 不支持的文件被静默丢弃

**现象**：上传 `.doc`、`.png` 之类的文件，界面上看起来"上传成功了"，实际后端
把它们过滤掉了，什么提示都没有，最后只报一句笼统的"没有成功处理任何文档"。

**证据**：原 `backend/app/api/graph.py` 上传循环里的 `if allowed_file(...)` 分支
（没有 else），无任何用户可见的反馈。

**建议**：已改为逐文件 `try/except` 收集结果，返回 `file_results` 数组（含每个文件
成功/失败与原因），前端把失败的条目写进系统日志；旧版 Office 给出「另存为」提示。

**代价**：已完成。

---

### 1.3 ★ 一个坏文件会拖垮整批上传

**现象**：一批上传 5 个文件，其中 1 个解析时抛异常（编码怪、文件截断、加密），
整个请求变成 500，**另外 4 个也一起丢了**，用户只看到一段 traceback。

**证据**：原 `backend/app/api/graph.py` 的 `FileParser.extract_text()` 调用没有
局部 `try/except`，异常直接冒泡到最外层的 500 处理器。

**建议**：已随 1.2 一并修复（逐文件 `try/except`）。

**代价**：已完成。

---

### 1.4 ★★ 本体的文本截断会饿死后排文档

**现象**：`MAX_TEXT_LENGTH_FOR_LLM = 50000` 是对**合并后**的文本一刀切。传一个
300KB 的 Excel + 一个 20KB 的 PDF，PDF 会**完全进不了本体生成**；而且切片会落在
表格中间，留下一个断头的管道表格让 LLM 误读。

**证据**：原 `backend/app/services/ontology_generator.py` 的 `_build_user_message()`
里 `combined_text[:MAX_TEXT_LENGTH_FOR_LLM]`。

**建议**：已改为按文档比例分配配额，每篇保底 4000 字符，截断点回退到块边界
（`_allocate_documents()`）。

**代价**：已完成，约 40 行。可进一步做"注水式"再分配（小文档用不完的配额转给大文档），
属于锦上添花。

---

### 1.5 ★ 重启后 `task_id` 全线失效，且有一处没有补偿

**现象**：`TaskManager` 是纯内存的（一个 dict + 一把锁）。后端一重启，所有任务 ID 失效。
`/api/simulation/prepare/status` 与 `/api/report/generate/status` 会去扫 `state.json`
和产物文件重新推断状态，**但 `/api/graph/task/{task_id}` 没有这类补偿**——如果构建图谱
期间后端重启了，前端会永远转圈，用户除了重新点一次构建别无他法。

**证据**：`backend/app/models/task.py`（`TaskManager` 无持久化）；
`backend/app/api/graph.py` 的任务查询接口只查内存。

**建议**：两个方向二选一——
（a）把 `TaskManager` 落盘（写 `uploads/tasks/{task_id}.json`，重启后回读）；
（b）给图谱任务查询加与另两个接口同样的"扫文件推断"补偿。
（a）更彻底，且能顺带解决进度丢失。

**代价**：中（约 60 行 + 过期清理策略）。

---

### 1.6 ★★ `fetch_all_edges` 没有数量上限

**现象**：`fetch_all_nodes` 有 `max_items=2000` 的保护，`fetch_all_edges` 却**没有**。
大图上一次性把所有边拉进内存（全景检索 `panorama_search` 就会走这条路），
可能直接吃满内存。

**证据**：`backend/app/utils/zep_paging.py:56-99`（节点有上限）vs `:102`（边无上限）。

**建议**：给 `fetch_all_edges` 加同样的 `max_items` 参数与截断日志。

**代价**：极小（约 5 行）。

---

### 1.7 ★★ `generate_python_code()` 产出的代码必然 ImportError

**现象**：`OntologyGenerator.generate_python_code()` 生成的本体模型代码里写着
`from zep_cloud.external_clients.ontology import ...`，而 `zep-cloud` 早已从依赖里移除。
这个功能一旦被调用必然失败。

**证据**：`backend/app/services/ontology_generator.py:453`。

**建议**：这个函数目前没有任何调用方（API 路径不走它）。要么删掉，要么把导入改成
直接输出 `pydantic.BaseModel` 子类（去掉 zep_cloud 依赖）。

**代价**：小。删掉更干净。

---

## 一·补、图谱会重复累积（2026-10-06 实测，严重）

### 1.8 ✅ 重复构建图谱会不断累积重复节点（2026-10-07 已修，附修复脚本）

**现象**：实测一个项目，13k 字符的文本产生了 **8167 个节点**（每 1.6 个字符一个实体）。
其中：
- `SAP` 有 **391 个副本**（391 个不同 uuid、同一个名字）
- `MES` 338 个、`WMS` 310 个
- **418 个名字存在重复**
- 同一概念被抽成中英两套标签（`Concept` 1632 + `概念` 437；`Organization` 1322 + `组织` 58）

**根因**（已定位到行）：
1. `deepseek_graph_extractor.py:247` 每次抽取都为实体生成**全新的 `uuid4()`**
2. 批内去重表 `name_to_uuid`（`:248`）**每次调用都从空开始**——它只在单次抽取内部有效
3. `local_graph_store.py:234` 的 `upsert_node` 以 **`uuid_` 作为主键**（`INSERT OR REPLACE`）

三者叠加的结果：**每点一次"构建图谱"，同样文本抽出的每个实体都会作为新行插入一次。**

**证据**：`episodes` 表里有 **534 条**记录，而该文本按 `chunk_size=500` 只值约 **29 个 chunk**
——即文本被抽取了约 **18 遍**（episodes 时间戳跨 02:32 到 14:45，与"反复点构建"吻合）。

**后果链**（这条链解释了本次观测到的大部分异常）：
```
重复构建 → 8167 节点
  → fetch_all_nodes 的 max_items=2000 把实体数截到 2000（静默，无提示）
  → 人设生成 = 2000 次 LLM 调用（实测跑到 562/2000 被中断）
  → 模拟只能用已完成的那部分（460 个 Agent），且两平台人设数还不一致
```

**建议**：
1. **节点身份应由 `(graph_id, name)` 决定，而不是 `uuid4`**。两条路径：
   - (a) 给 `nodes` 加 `UNIQUE(graph_id, name)` 约束，`upsert_node` 改为按 `(graph_id, name)` upsert
     并复用已有 uuid
   - (b) 抽取前先查同图已有节点，把新抽到的实体归并到已有 uuid 上
   (a) 更彻底，但需要一次 schema 迁移
2. 构建前若该图已有节点，应**明确询问**是"继续累积"还是"从头重建"，而不是默默继续
3. 顺带：`fetch_all_nodes` 的 2000 上限被静默触发（只见一行 WARNING 日志），
   界面上应当明确显示"实体数已达上限，实际图谱更大"

**代价**：中（核心修复约 30 行 + 迁移；附带提示很小）。

**已完成（2026-10-07）**：采用了建议里的 (a) 方向，并顺带修了同一病灶的边侧：

1. `nodes` 加 `name_key` 列（= `lower(trim(name))`），`upsert_node` 改为按
   `(graph_id, name_key)` 归并、**复用已有 uuid**，并返回"库内规范节点"。
   老库通过 `ALTER TABLE ADD COLUMN` + 回填自动迁移。
2. **关键连带改动**：`deepseek_graph_extractor._process_extraction_result` 现在使用
   `upsert_node` 返回的 uuid 去连边。否则复用 uuid 会把本轮新建的边指向被合并掉的
   旧 uuid，产生孤儿边——这也是为什么必须同时改抽取器，而不是只改存储层。
3. `upsert_edge` 同样按 `(graph_id, source, target, name, **fact**)` 归并。
   带上 `fact` 是保守选择：只有事实描述完全一致的边才合并，带时间窗
   （`valid_at`/`invalid_at`）的不同事实不会被误并。
4. 迁移会尝试建立两个唯一索引（`idx_nodes_graph_name_key`、`idx_edges_identity`）；
   若历史数据已有重复，则**降级为普通索引并告警**，绝不擅自删数据。
5. 历史数据清理脚本 `backend/scripts/dedupe_graph.py`：默认演练，`--apply` 才写入
   且**先自动备份数据库**。合并时优先保留被边引用最多的 uuid 并把边重新指向它。

实测本机那份被污染的库（比本文档写作时又涨了）：**9336 个节点实际只有 960 个名字**，
`SAP` 440 个副本；演练结果 9336 → 1389 个节点，边 6681 → 5981（重指向 6209、删除 700）。

测试：`backend/tests/test_graph_dedupe.py`（10 项，无网络），覆盖"反复构建不增长"、
"边不指向幽灵节点"、老库迁移降级、修复脚本端到端。**仍未做**：构建前询问
"继续累积还是从头重建"、以及实体数撞上 2000 上限时的界面提示（见下）。

---

## 二、性能与成本

### 2.1 ★★ 全流程没有 SSE / WebSocket，共 8 处轮询

**现象**：5 个步骤依赖 8 个 `setInterval` HTTP 轮询，最短间隔 **1.5 秒**
（报告页同时轮询 agent-log 与 console-log）。长时间生成时产生大量空请求，
且进度反馈有最多一个轮询周期的延迟。

**证据**：`frontend/src/views/MainView.vue`（2s + 10s）、
`components/Step2EnvSetup.vue`（2s / 3s / 2s）、`components/Step3Simulation.vue`（2s / 3s）、
`components/Step4Report.vue`（2s / 1.5s）。

**建议**：后端已经有日志文件的增量游标（`from_line`）语义，改 SSE 成本很低——
加一个 `text/event-stream` 端点持续 tail 对应文件即可，前端用 `EventSource` 替换
`setInterval`。轮询可保留为降级路径。

**代价**：中（后端约 80 行，前端每个组件约 20 行）。

---

### 2.2 ★★ 图谱抽取的字符预算大量浪费

**现象**：`DeepSeekGraphExtractor` 把每个 episode 的文本截断到 3000 字符，
但上层 `GraphBuilderService` 是按 `chunk_size=500` 分块、每 3 块打包成一个 episode
——**每批最多只有 1500 字符，截断形同虚设**，而批次之间的 `time.sleep(1)`
则在真金白银地浪费时间。

**证据**：`backend/app/services/deepseek_graph_extractor.py:199`（`text[:3000]`）；
`backend/app/services/graph_builder.py` 的 `add_text_batches(batch_size=3)`
与批间 `time.sleep(1)`。

**建议**：让 `batch_size` 与截断上限对齐（例如 batch_size=6、上限仍是 3000），
并把固定 `sleep(1)` 换成"仅在遇到限流错误时退避"。

**代价**：小，但会改变图谱构建的调用节奏，需要跑一次小样本对比抽取质量。

---

### 2.3 ✅ 上传失败会把整个文件重传 3 次

**现象**：`generateOntology` 被包在 `requestWithRetry(fn, 3, 1000)` 里。上传 40MB
材料时若超时，会把整个 multipart **重传三遍**；而 4xx 这类"请求本身有问题"的
错误重试更是毫无意义（本次新增的"全部文件不支持"就走 400）。

**证据**：`frontend/src/api/graph.js` 的 `generateOntology` 调用；
`frontend/src/api/index.js` 的 `requestWithRetry`。

**建议**：已加 4xx 不重试的判断。更彻底的做法是让上传请求完全不重试。

**代价**：已完成（约 4 行）。

---

### 2.4 ★★ 上传解析是同步的，大文件会顶到超时

**现象**：文件解析（含 OCR）在上传请求里同步完成，axios 超时 300 秒。
一份 30 页扫描件 × 约 1 秒/页，再加上大 Excel 的流式读取，很容易吃掉一大块预算。

**证据**：`backend/app/api/graph.py` 的 `/ontology/generate` 同步解析；
`frontend/src/api/index.js` 的 `timeout: 300000`。

**建议**：复用项目里已有的后台任务模式——`/api/graph/build` 就是起 daemon 线程后
立刻返回 `task_id`、前端轮询进度。让上传也走同一套，顺带获得"解析进度"反馈。

**代价**：大（后端改造 + 前端 Step1 增加解析阶段的进度 UI）。建议与 2.1 一起做。

---

## 三、可维护性

### 3.1 ★★★ `views/Process.vue` 是 2070 行死代码

**现象**：`Process.vue`（2070 行）从未被路由引用——`router/index.js` 里
`name: 'Process'` 实际指向的是 `MainView.vue`。它包含一份**旧版的重复上传/构建逻辑**，
容易误导后来者（也误导过本次分析）。

**证据**：`frontend/src/router/index.js` 的 import 语句；全项目无其它引用。

**建议**：删除。如果担心其中的实现细节有价值，先 `git rm` 后在 commit message
里留个引用即可（Git 历史还在）。

**代价**：零（纯减法）。

---

### 3.2 ★★★ 巨型单文件

**现象**：

| 文件 | 行数 |
|---|---|
| `frontend/src/components/Step4Report.vue` | 5162 |
| `backend/app/api/simulation.py` | 2716 |
| `frontend/src/components/Step2EnvSetup.vue` | 2605 |
| `frontend/src/components/Step5Interaction.vue` | 2584 |
| `backend/app/services/report_agent.py` | 2572 |

**建议**：优先拆 `Step4Report.vue`——它同时承担了轮询、日志解析、Markdown 渲染、
布局切换四件事，是"改一处怕碰坏三处"的典型。按职责拆成
`useReportLog()`（组合式函数，管轮询与增量解析）+ 展示组件 + 渲染工具。

**代价**：中偏大，但收益是后续每次改动的成本都下降。

---

### 3.3 ★★★ Markdown 渲染手写了两份，且没有转义保护

**现象**：`Step4Report.vue` 与 `Step5Interaction.vue` 各有一份约 100 行的正则
Markdown 渲染器（几乎相同），`v-html` 直接注入。既是重复代码，又存在注入面
（当前内容来自自己后端的 LLM 输出，风险有限，但不够稳）。

**证据**：`Step4Report.vue` 与 `Step5Interaction.vue` 各自的 `renderMarkdown` 函数。

**建议**：换成 `marked` + `DOMPurify`，两处共用。包体增量很小。

**代价**：小。

---

### 3.4 ★★★ 语言清单与实际翻译文件不一致

**现象**：`locales/languages.json` 声明了 7 种语言（zh/en/es/fr/pt/ru/de），
但只有 `zh.json` 和 `en.json` 存在。因为 `frontend/src/i18n/index.js` 是按文件
是否存在来决定要不要注册语言的，所以切换器**静默地只显示 2 种**，不报错也不提示。

**证据**：`locales/languages.json` vs `locales/` 下的实际文件。

**建议**：要么把多余的语言声明删掉（诚实），要么补上占位翻译文件再逐步填充。

**代价**：极小。

---

### 3.5 ★★★ 死导出与缺失的测试

- `frontend/src/api/report.js` 的 `getReportStatus` 导出但全项目未使用 →
  已被后端同类接口取代，可删。
- **项目没有任何自动化测试**：`pytest` 声明在 `backend/pyproject.toml` 的 dev 依赖里，
  但仓库里一个 `test_*.py` 都没有（`backend/scripts/test_profile_format.py` 是个手动脚本，
  不是测试）。也没有 lint / CI 配置。

**建议**：先补最小可用的测试面——针对纯函数（`split_text_into_chunks`、
`render_markdown_table`、`_allocate_documents`、各解析器）写不依赖网络的单元测试，
成本低、回归价值高。`backend/scripts/test_multi_format.py` 已经是一个可用的起点
（它本身就是断言式的，改成 pytest 用例即可）。

**代价**：小（起步）到中（覆盖核心流程）。

---

### 3.6 ★★★ 意外存在的 `unstructured` 不可用

**现象**：`backend/.venv` 里有 `unstructured==0.13.7`，看起来像是一个现成的多格式
解析库——但它其实是 `camel-ai` 的 `rag`/`document-tools` extra 传递进来的
（`pyproject.toml` 与 `requirements.txt` 都没声明），而且由于缺少
`python-docx` / `python-pptx` / `openpyxl`，它的 `partition/docx.py`、`pptx.py`、
`xlsx.py` **实际全都跑不起来**。

**建议**：两条路——
（a）**已采纳**：显式声明 `python-docx` / `python-pptx` / `openpyxl`，不依赖这个传递依赖；
（b）将来若想要 odt / rtf / html / epub / eml 等更宽的格式面，可以显式声明
`unstructured` 及其 extra，用它换广度，但要接受它的依赖体积。

**代价**：取决于是否要做（b）。

---

## 二·补、运行时可靠性（2026-10-06 实测发现）

以下三条是在真实运行中观察到的，都会导致"应用看起来活着、实际不能用"，
比崩溃更难排查。

### 2.5 ★ 调试重载会让模拟子进程变成孤儿

**现象**：后端开着 debug 自动重载时，只要编辑任何一个被导入的 `.py` 文件，Flask 就会重启。
而 `SimulationRunner` 用**类级字典**（`_processes` / `_monitor_threads`）跟踪模拟子进程——
进程一重启，这些引用全丢。子进程是用 `start_new_session=True` 启动的，因此**不会随父进程退出**，
就此变成孤儿（`ppid=1`）继续占着 CPU 跑。

实测：同一份模拟 `sim_2f0725c1b70c` 上同时存在**两个** `run_parallel_simulation.py` 进程，
其中一个已孤儿化约 1 小时。

**后果**：两个进程写同一个 `actions.jsonl` 与同一份 OASIS `*.db`，模拟数据可能互相污染；
且状态永远无法收敛。

**建议**：
1. 把进程登记表落盘（`uploads/simulations/<sim_id>/runner.pid` + 启动时间戳），
   启动时扫描并回收"父进程已死"的孤儿
2. 或/并且：开发时用 `FLASK_DEBUG=false` 跑长时间模拟（重载与长任务天然冲突）
3. 在 `docs/MANUAL.md` 的排查表里加一条：模拟卡在 running、且发现多个同名子进程时怎么处理

**代价**：小到中。

### 2.6 ★ 模拟跑完最后一轮后，状态不会收敛到 completed

**现象**：实测一次 20 轮的模拟，`run_state.json` 里 `current_round` 已到 `20/20`，
但 `runner_status` 始终停在 `running`。原因是监控线程判定完成的依据是
**两个平台都写出 `simulation_end` 事件**（或子进程退出）；实测中 Reddit 写了、
Twitter 没写，于是永远等下去。前端因此会一直显示"运行中"。

**建议**：给监控线程加一条兜底判据——「轮数已达 `total_rounds` 且 N 秒内
`actions.jsonl` 无新增」即判定为完成（或至少转成一个明确的 `stalled` 状态），
而不是无限等待。

**代价**：小。

### 2.7 ★★ 后端被长任务占满时完全不响应 HTTP

**现象**：在一次 2000 实体的批量人设生成期间，`/health` 稳定超时（试用 6s / 30s 均无响应），
但进程内部仍在推进（日志可见进度从 341 涨到 562）。也就是说**服务在干活但拒绝服务**，
从外部看与"卡死"无法区分——这正好也是本次会话开始时那个实例的症状。

**影响**：前端所有轮询一起超时，用户看到的就是"界面卡住"；也无法通过 `/health` 判断
后端到底活着没有。

**建议**：
1. 确认 Werkzeug 开发服务器的线程模型是否真的能并发响应（`threaded=True` 理论上可以，
   实测未响应，需要进一步定位——建议在长任务期间用 `py-spy dump` 抓栈确认是 GIL
   争用、线程池耗尽，还是某个锁被持有）
2. 把批量人设生成改到**子进程**或独立 worker，不要占用 Web 进程
3. `/health` 改为完全不碰任何共享状态（当前已是），并考虑加一个只读的 `/health/deep`

**代价**：诊断中。

### 2.8 ★★ 2000 个实体 = 2000 次 LLM 调用，且没有事前提示

**现象**：实测的这份材料抽出了 **2000 个实体**（恰好撞上 `fetch_all_nodes` 的 2000 上限，
说明图谱本身可能更大）。人设阶段就是 2000 次 LLM 调用；若再跑 10 轮双平台模拟，
按新加的预估器算是 **40000 次**调用。

这正是 C4（成本护栏）要解决的问题——护栏已经做好（启动前会拦截并给出预估），
但**人设生成阶段的成本没有护栏**，它在 `/prepare` 里直接开跑。

**建议**：把 `estimate_for_simulation` 的护栏同时挂到 `/api/simulation/prepare` 上，
在生成 2000 份人设之前先告诉用户"这一步将发起约 2000 次调用"。

**代价**：小。

---

## 四、体验与安全

### 4.1 ✅ 默认监听 `0.0.0.0` + CORS 放开 `*`（2026-10-07 已修）

**现象**：后端默认绑 `0.0.0.0`，且 `/api/*` 的 CORS 允许任意来源，接口**没有任何鉴权**。
在咖啡厅、共享办公等同一局域网里，任何人都能：调用你的 LLM（花你的钱）、
读取你上传的所有材料（包括 `graph_id` 与报告）、甚至删除项目。

**证据**：`backend/run.py:40`（`FLASK_HOST` 默认 `0.0.0.0`）、
`backend/app/__init__.py:43`（`origins: "*"`）。

**建议**：本地应用应当默认 `127.0.0.1`——把默认值改成 `127.0.0.1` 即可，
需要局域网访问的人显式设置 `FLASK_HOST=0.0.0.0`。CORS 同理收紧到
`http://localhost:3000`。

**代价**：极小（两个默认值），但**安全收益最大**。

**已完成（2026-10-07）**：`run.py` 的 `FLASK_HOST` 默认改为 `127.0.0.1`（设为 `0.0.0.0`
时打印一条警告）；`app/__init__.py` 的 CORS 默认只放行 `http://localhost:3000` 与
`http://127.0.0.1:3000`，可用 `CORS_ORIGINS`（逗号分隔，或 `*`）覆盖。

验证（起在 :5002 实测）：`lsof` 显示只监听 `127.0.0.1:5002`；`Origin: http://localhost:3000`
的预检返回 `Access-Control-Allow-Origin: http://localhost:3000`，而 `Origin: http://192.168.1.50:3000`
不返回该头（被拒）。**仍未做**：接口本身依然没有鉴权——本地单用户可接受，但若要局域网共享，
需要补 token 鉴权。

---

### 4.2 ★★ 上传体验：没有进度、没有大小提示、没有去重

**现象**：
- 没有上传进度条（全项目 grep 不到 `onUploadProgress`），大文件上传时界面只有一个
  转圈，用户不知道是否卡住
- 后端有 50MB 上限，但前端不校验；超出时后端返回 413，前端只显示一句笼统的错误
- 同一文件可以被重复添加多次，没有去重

**证据**：`frontend/src/views/Home.vue` 的文件处理逻辑；`backend/app/config.py:47`。

**建议**：在 `addFiles` 里校验 `file.size`、按 `name+size` 去重并提示，
再给 `generateOntology` 挂 `onUploadProgress` 把百分比接到界面上。

**代价**：小（前端约 40 行）。

---

### 4.3 ★★ XML 解析加固（低优先）

**现象**：docx / pptx / xlsx 都是 ZIP + XML，攻击面是解压炸弹与 XML 解析器滥用。
当前实现已经有几层防护（`read_only` 流式 + 行列上限 + 不用 `extractall`
+ `keep_links=False` + 不读宏），项目是本地单用户应用，实际风险低。

**建议**：若要进一步加固，加 `defusedxml` 并让 lxml/etree 走它。
优先级低于 4.1。

**代价**：极小（加依赖），但收益在本地场景下有限。

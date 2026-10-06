# MacFish 操作手册

> 面向使用者：怎么装、怎么配、怎么跑完一次完整推演、出问题怎么查。
> 想了解内部原理请看 [ARCHITECTURE.md](./ARCHITECTURE.md)，想参与改进请看 [OPTIMIZATION.md](./OPTIMIZATION.md)。

---

## 1. 环境要求

| 工具 | 版本 | 检查命令 | 说明 |
|---|---|---|---|
| Node.js | ≥ 18 | `node -v` | 前端运行环境 |
| Python | ≥ 3.11 且 ≤ 3.12 | `python3 --version` | 后端；3.13 不支持 |
| uv | 最新 | `uv --version` | Python 包管理器，`brew install uv` |

macOS 上系统自带的 `python3` 通常是 3.9，**太旧**。不需要手动升级——`uv` 会自动
下载并使用它管理的 Python 3.12 解释器。

> **OCR 功能仅 macOS 可用**（走系统内置的 Vision 框架）。其它平台仍可上传
> PDF / Word / PPT / Excel / 文本，只有图片与扫描件需要另配云端视觉 API。

---

## 2. 安装

```bash
cd ~/MacFish

# 一次性装齐：根目录 + 前端 + 后端
npm run setup:all
```

如果已经装过、只想补装本次新增的依赖（Office 解析 + 本地 OCR）：

```bash
cd backend
uv sync --extra ocr-macos     # macOS
uv sync                        # 其他平台（不含本地 OCR）
```

`--extra ocr-macos` 只在 macOS 上有意义：它装的是 `pyobjc-framework-Vision` 与
`-Quartz`，会拖入大半个 pyobjc 运行时，所以没有放进默认依赖。

---

## 3. 配置

### 3.1 走 `.env`（首次配置）

```bash
cd ~/MacFish
cp .env.example .env
```

编辑 `.env`，至少填好 LLM 部分：

```env
LLM_PROVIDER=network
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat
```

支持任何 OpenAI 兼容接口，换供应商只改这三行：

| 供应商 | `LLM_BASE_URL` | `LLM_MODEL_NAME` |
|---|---|---|
| DeepSeek（默认） | `https://api.deepseek.com` | `deepseek-chat` |
| 阿里百炼 Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b`（并把 `LLM_PROVIDER=local`）|

**`.env` 里的必填项**：

- `LLM_API_KEY` —— 当 `LLM_PROVIDER=network` 时必填。
- 不再需要 `ZEP_API_KEY` —— 知识图谱已完全本地化（SQLite + `LocalGraphClient`，
  它根本不使用任何 api_key）。历史版本曾把 `ZEP_API_KEY` 作为启动校验的必填项，
  现已彻底移除；如果你的 `.env` 里还留着它，删掉即可，不影响启动。

### 3.2 界面里改（运行时配置）

启动后点右上角设置图标，可以改 LLM 供应商与 OCR 引擎。这些改动写进仓库根的
`macfish_settings.json`，**优先级高于 `.env`**——也就是说界面里改过一次之后，
再改 `.env` 就不生效了（除非删掉那个 JSON 文件）。

### 3.3 文档解析 / OCR 配置

设置面板底部的「文档解析」一节：

| 选项 | 说明 |
|---|---|
| **OCR 引擎** | `自动`（默认，本地优先）/ `本地 Vision`（只用本地）/ `视觉 API`（只用云端）/ `关闭` |
| **扫描版 PDF 自动 OCR** | 默认开启。逐页判定，约 1 秒/页 |
| **单份文档最大 OCR 页数** | 默认 30，防止一本扫描书把请求拖死 |
| **视觉 API 配置** | 仅当引擎选 `视觉 API` 时才需要填 |

面板上会实时显示两个引擎的可用性（`本地 Vision: 可用 · 视觉 API: 未配置`），
点「测试 OCR」可以在界面里直接验证接线——它会现生成一张带文字的图并识别回来。

---

## 4. 启动

```bash
cd ~/MacFish
npm run dev
```

一条命令同时拉起两个服务：

| 服务 | 地址 |
|---|---|
| 前端界面 | http://localhost:3000 |
| 后端 API | http://localhost:5001 |

只起其中一个：`npm run backend` / `npm run frontend`。

> **改过依赖或后端代码后要重启 `npm run dev`。** 后端开了 debug 自动重载，
> 但 `uv sync` 换掉了虚拟环境里的包之后，正在跑的进程需要重启才能用上。

---

## 5. 五步操作走查

### 第一步：创建项目（上传种子材料）

在首页输入框里用自然语言写清楚**你想预测什么**，然后上传材料。

**支持的文件格式：**

| 类别 | 扩展名 | 说明 |
|---|---|---|
| 文档 | `.pdf` | 有文字层直接读；纯扫描页自动 OCR |
| Word | `.docx` | 保留标题层级、表格、文本框、页眉页脚 |
| PowerPoint | `.pptx` | 每页一个章节，含表格、图表数据、演讲者备注 |
| Excel | `.xlsx` `.xlsm` | 每个工作表一个章节，输出为 Markdown 表格 |
| 图片 | `.png` `.jpg` `.jpeg` `.heic` `.heif` `.tiff` `.tif` `.bmp` `.webp` `.gif` | 走 OCR 提取图片里的文字 |
| 文本 | `.txt` `.md` `.markdown` `.csv` | 直接读取，自动探测编码 |

**旧版 Office 格式（`.doc` / `.ppt` / `.xls`）不支持解析**，会明确提示你用 Office 或
WPS 另存为 `.docx` / `.pptx` / `.xlsx` 后重新上传——这是有意的，旧二进制格式的解析
需要额外依赖且容易出错。

其它限制：

- 单请求总大小上限 **50 MB**（超出会被后端拒绝，前端目前没有提前提示）
- 大表格会被截断：每个工作表最多 **200 行 × 30 列**，工作簿最多 **20 个表**，
  PPT 最多 **200 页**。截断处会明确写出"共 N 行，已展示前 M 行"
- 上传成功的文件如果个别被跳过（例如加密文档），**系统日志里会写明原因**

> 材料越结构化越好。带标题层级的 Word、分页清晰的 PPT、表头规范的 Excel，
> 抽出来的实体和关系质量明显高于一大段没有结构的纯文本。

### 第二步：生成本体

系统把材料交给 LLM 分析，产出 10 个实体类型与若干关系类型。这一步通常十几秒。

你可以在界面上查看生成的类型定义。**类型定义直接决定后续 Agent 的构成**——
如果抽出来的实体类型不符合你的预期，与其在模拟阶段补救，不如在这里调整后重跑
（更好的做法是回去把种子材料写得更聚焦）。

### 第三步：构建知识图谱

文档自动分块后逐批送 LLM 抽取实体与关系，写入本地 SQLite。

耗时与材料量成正比：**这是整个流程里最慢的一步之一**，材料多时可能数分钟到十几分钟。
界面会显示节点与边的数量，可以点开查看具体实体。

### 第四步：环境搭建

基于图谱实体生成 Agent 人设（姓名、简介、MBTI、职业、立场、影响力权重），
再做模拟的时间流速、事件、平台推荐权重配置。

人设是**并行生成、实时落盘**的，所以你能看到它们一个个长出来，不必干等。

可选参数：模拟轮数（默认按配置里的时间跨度算，可手动指定上限）。

### 第五步：开始模拟

双平台（Twitter / Reddit）并行模拟。每个 Agent 按自己的人设与活跃度自主发帖、
点赞、转发、评论。界面实时显示两个平台各自的轮数、模拟小时数、动作条数。

耗时取决于轮数与 Agent 数量，从几分钟到几十分钟不等。中途可以停止。
如果开了「图谱记忆更新」，Agent 的行动会分批写回图谱，形成时序记忆。

### 第六步：生成报告

Report Agent 从图谱中检索信息、必要时直接采访模拟中的 Agent，逐章写出报告。
侧边能实时看到 Agent 的思考与工具调用日志，报告章节是流式出现的。

报告可下载为 Markdown。

### 第七步：深度互动

- **和 Report Agent 对话**：追问报告里的结论、让它再深挖某个点
- **采访任意 Agent**：直接问模拟世界里的某个人物（例如"你对这次降价怎么看"），
  回答基于它在模拟中形成的人设与经历

采访依赖模拟子进程还活着。如果模拟结束后关闭了环境（或后端重启过），采访会失败。

---

## 6. 数据与目录

| 想看什么 | 去哪 |
|---|---|
| 知识图谱 | `backend/uploads/graphs.db`（SQLite，可直接用 DB 工具打开） |
| 上传的原始文件 | `backend/uploads/projects/{project_id}/files/` |
| 提取并合并后的文本 | `backend/uploads/projects/{project_id}/extracted_text.txt` |
| 模拟配置与人设 | `backend/uploads/simulations/{sim_id}/` |
| 报告 | `backend/uploads/reports/{report_id}/full_report.md` |
| 后端日志 | `backend/logs/YYYY-MM-DD.log` |
| 运行时设置 | `macfish_settings.json`（仓库根，含明文密钥，勿外传） |

### 清理

```bash
# 只清某一次模拟的中间产物（保留配置与人设）
#   删 simulations/{sim_id}/ 下的 run_state.json、simulation.log、*.db、actions.jsonl

# 彻底重置图谱（会让已有项目失去图谱，需要重新构建）
rm backend/uploads/graphs.db*

# 清空全部项目与产物
rm -rf backend/uploads/projects backend/uploads/simulations backend/uploads/reports
```

在界面的历史记录里删除单个项目是最安全的做法。

---

## 7. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 后端启动即退出，日志说 `LLM_API_KEY 未配置` | `LLM_PROVIDER=network` 时必须配置 `LLM_API_KEY`。（旧版还会要求 `ZEP_API_KEY`，该要求已移除） |
| 图谱里同一实体有几百个副本 | 旧版 `upsert_node` 以每轮新 uuid 为主键导致的累积，代码已修复。历史数据用 `cd backend && uv run python scripts/dedupe_graph.py --apply` 清理（会先自动备份数据库） |
| 启动报 `Port 5001 is in use` | 已有实例在跑。`lsof -nP -iTCP:5001 -sTCP:LISTEN` 找到 PID 后 `kill`，或先停掉上一个 `npm run dev` |
| 改完 `.env` 不生效 | 界面里保存过设置，`macfish_settings.json` 优先级更高。删掉它或直接在界面里改 |
| 上传的图片没有任何内容 / 提示"需启用 OCR" | 设置 →「文档解析」里 OCR 引擎被设成了"关闭"，或选"视觉 API"但没配密钥 |
| 扫描版 PDF 提取不出文字 | 确认「扫描版 PDF 自动 OCR」已开启；确认页数没超过上限 |
| `.doc` 上传被拒 | 这是设计如此。用 Office/WPS 另存为 `.docx` 后重新上传 |
| Excel 提取出来是空的 | 表格可能只有公式、没有缓存值（脚本生成的文件常见）。系统会自动改读公式原文并标注；若仍然为空，试试用 Excel 打开另存一次 |
| Excel 内容不完整 | 单表超过 200 行 / 30 列会被截断。拆分工作表或先做数据透视 |
| 上传 50MB 以上的文件失败 | 后端硬上限 50MB。先裁剪材料 |
| 图谱构建到一半，前端一直转圈 | 后端中途重启过，`task_id` 是内存态已失效。重新点一次构建 |
| 模拟跑得很慢 | 在 `.env` 里调小 `OASIS_DEFAULT_MAX_ROUNDS`（默认 10），或在第四步手动把轮数调小 |
| 模拟启动后立刻失败 | 看 `backend/uploads/simulations/{sim_id}/simulation.log` 的末尾，那里会记下子进程的报错 |
| 采访 Agent 报"环境未运行" | 模拟子进程已退出或环境被关闭。重新跑一次模拟 |
| 报告内容与预期不符 | 回到第二步检查本体类型定义；本体不对，后面全歪 |
| 返回的 `model` 字段显示 `deepseek-flash` 而配置是 `deepseek-chat` | 这是上游的路由行为，不是配置错误 |
| 请求 DeepSeek 报余额/额度错误 | 换一个供应商（改 `.env` 或在界面设置里改） |

### 看日志

```bash
# 后端日志
tail -f backend/logs/$(date +%F).log

# 某个模拟的子进程输出
tail -f backend/uploads/simulations/{sim_id}/simulation.log
```

---

## 8. 回测（评估预测质量）

MacFish 现在带一个回测框架，用来回答一个此前无法回答的问题：**它到底有多准？**

回测的输入是**案卷**：一份"截至某个日期的种子材料" + "该日期之后真实发生了什么"。
跑完之后给出分数，以及多次运行之间结论的稳定性。

> **仓库自带的两个演示案卷是合成的**（`synthetic_*`），种子材料与"真实结果"都由脚本
> 编造，只用来验证框架本身能跑通，**不能**用来评价预测能力。真实案卷需要你用自己的
> 历史材料录入——见 `backend/benchmarks/README.md`。

```bash
cd backend

# 看有哪些案卷、校验状态如何
uv run python scripts/run_benchmark.py list

# 只做防泄漏校验与成本预估，不发起任何 LLM 调用
uv run python scripts/run_benchmark.py plan --cases <case_id>

# 真实回测（必须显式给出预算上限，这是费用同意闸门）
uv run python scripts/run_benchmark.py run --cases <case_id> --repeats 3 --budget-calls 20000

# 打印记分卡
uv run python scripts/run_benchmark.py report --run <run_id>

# 离线端到端验证框架本身（桩件，零 LLM 调用）
uv run python scripts/run_benchmark.py verify
```

### 输出怎么读

**「可用于头条统计的尝试」才是有效样本。** 一个尝试要合格必须同时满足四条：
跑通、通过防泄漏校验、**不是合成案卷**、没被轮数上限截断。其余一律进不合格桶并单列，
不参与任何准确率计算。

指标分两类，别混用：

| 类别 | 含义 | 能说明什么 |
|---|---|---|
| **关键词代理指标**（方向命中率、角色召回率等） | 报告有没有覆盖该覆盖的内容 | **不能**说明预测对不对，只说明管线没漏东西 |
| **LLM 裁判分数**（`--judge`，需要更完整的实现） | 对照真实结果逐项判定 | 更接近有效性，但仍需要人工抽查 |

**稳定性**（`--repeats N`）比单次分数更有价值：跑三次，报告里哪些结论每次都出现、
哪些摇摆不定，一目了然。摇摆的结论不该被当作依据。注意判断稳定性时三次运行的
模型/配置必须一致，否则框架会标记为「不可比」并拒绝给数字。

### 防泄漏校验

回测最容易犯也最致命的错误是把结果写进输入——那样模型"预测"得极准，分数却毫无意义。
所以案卷在运行前要过五道检查（哈希完整性、日期顺序、时间线日期扫描、结果定义词扫描、
结果数量级扫描），未通过的一律**拒绝执行**（除非 `--allow-unvalidated`，那样分数不计入
头条统计）。

---

## 9. 费用与规模提示

- 每次完整推演会发起大量 LLM 调用：本体 1 次、图谱每个批次 1 次、
  每个人设 1 次、模拟中**每个 Agent 每一轮** 1 次、报告每章 3~5 次工具调用。
  **模拟阶段是费用大头**，开销与 `Agent 数 × 轮数` 成正比。
- 想低成本试跑：材料给少一点、第四步把轮数调到 3~5、先用小样本验证本体质量。
- Agent 数量由图谱实体数决定。想要"百万级 Agent"的规模，需要相应体量的材料，
  以及相应的 LLM 预算。

---

## 10. 英文界面

右上角可切换 中文 / English。界面文案与后端返回的提示语都会跟着变。

注意 `locales/languages.json` 里还声明了另外 5 种语言（es/fr/pt/ru/de），
但对应的翻译文件并不存在，所以切换器实际只提供中文与英文。

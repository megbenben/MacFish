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

## 🔧 这个 fork 改了什么

本 fork 把整条流水线重写为**直连单一 LLM 供应商 + 本地存储**，并修掉了一批通过审计原代码发现的
正确性问题。下面每一条都在本地用离线测试套件验证过（不消耗任何 LLM 调用）。

### 一、直连 DeepSeek API —— 不再依赖 Zep Cloud 与外部图数据库

知识图谱原本依赖 Zep Cloud，意味着第二个供应商、第二个 API Key，并且那个 Key 一旦缺失就
**直接起不来**。现在完全本地化：

- 抽取由 **DeepSeek**（`deepseek-chat`）通过标准 OpenAI 兼容接口完成
- 图谱存储是**本地 SQLite 文件**（`backend/uploads/graphs.db`）
- **不再需要 `ZEP_API_KEY`**，它也已从启动校验里移除
- 任何 OpenAI 兼容的接口都可以，改 `LLM_BASE_URL` / `LLM_MODEL_NAME` 即可

### 二、多格式文档摄入

种子材料不再限于 PDF / Markdown / TXT：

| 类别 | 格式 |
|---|---|
| 文档 | `.pdf`（文字层，扫描页可选 OCR）、`.docx`、`.pptx`、`.xlsx`、`.xlsm` |
| 文本 | `.txt`、`.md`、`.markdown`、`.csv` |
| 图片 | `.png`、`.jpg`、`.jpeg`、`.heic`、`.heif`、`.tiff`、`.tif`、`.bmp`、`.webp`、`.gif`（走 OCR） |

- OCR 使用 **macOS 本地 Vision 框架**，免费、离线、无需密钥（`uv sync --extra ocr-macos`）
- 也可以改用视觉大模型接口
- 旧版 Office 二进制格式（`.doc` / `.ppt` / `.xls`）会被明确拒绝并提示「另存为 .docx/.pptx/.xlsx」，
  而不是静默失败
- 每个文件的解析结果都会回报，一个坏文件不再拖垮整批上传

### 三、代码审计带来的工程修复

| 方面 | 改了什么 |
|---|---|
| 图谱重复累积 | 节点身份从「每次抽取生成新 `uuid4()`」改为 `(graph_id, name)`。反复重建会把每个实体重新插入一遍——实测有个库**9336 行节点实际只有 960 个不同名字**。附迁移脚本 `scripts/dedupe_graph.py` |
| 模拟生命周期 | 卡住的模拟不再永远停在 "running"；后端重启遗留的孤儿子进程会被识别并回收；主动停止不再被误报成失败 |
| 成本护栏 | 人设生成与模拟启动前都会给出预估调用次数，超出设置的预算上限时先拦下 |
| 任务持久化 | 长任务跨后端重启存活，不再让界面永远转圈 |
| 安全默认值 | 后端默认只绑 `127.0.0.1`，CORS 只放行本地开发源 |
| 测试 | `pytest` 从 **0 个测试变成 92 个**，另有离线回测框架（39 项检查）、多格式摄入检查（11 例）、前端 Markdown 检查（18 例） |

## ⚡ 概述

**MacFish** 是一个多智能体预测引擎。它从现实世界提取种子信息（突发新闻、政策草案、金融信号，
或任意文档），构建出一个平行数字世界：大量拥有独立人设与记忆的智能体在模拟社交平台上自由互动。
你可以在运行中注入变量，观察情势如何演化。

> **你提供：** 种子文档 + 用自然语言描述的预测需求
> **MacFish 返回：** 一份详细的预测报告，以及一个可深度交互的模拟世界

## 🔄 工作流

1. **图谱构建** —— 文档解析、实体/关系抽取、GraphRAG 构建
2. **环境搭建** —— 人设生成与 Agent 配置
3. **模拟推演** —— Twitter/Reddit 双平台并行模拟，动态更新时序记忆
4. **报告生成** —— 带检索工具集的 ReportAgent
5. **深度互动** —— 与模拟世界中的任意个体对话，或与 ReportAgent 对话

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

### 第 1 步 —— 创建项目
打开 <http://localhost:3000>，上传种子文档（格式见上表）或直接粘贴文本，并描述你的预测需求。

### 第 2 步 —— 生成本体
DeepSeek 分析材料后给出实体类型（Person、Organization、Event、Concept…）与关系类型。
确认前可以自行调整。

### 第 3 步 —— 构建知识图谱
文档被切块后交给 DeepSeek 抽取实体与关系，结果写入 `backend/uploads/graphs.db`，
可在界面里浏览节点与边。

### 第 4 步 —— 运行模拟
由图谱实体生成人设，Agent 在模拟的 Twitter/Reddit 平台上自主互动，
其行为会作为时序记忆写回图谱。

### 第 5 步 —— 生成报告
Report Agent 从知识图谱中检索并撰写分析报告（含情景树）。之后可以与任意 Agent 对话，
也可以直接与 Report Agent 对话。

### 数据存放位置

| 数据 | 位置 |
|---|---|
| 知识图谱 | `backend/uploads/graphs.db` |
| 上传文件与项目 | `backend/uploads/projects/` |
| 模拟运行 | `backend/uploads/simulations/` |
| 报告 | `backend/uploads/reports/` |
| 运行时设置（含你的 Key） | `macfish_settings.json`（已被 gitignore） |

### 常见问题

| 现象 | 处理 |
|---|---|
| DeepSeek 额度用尽 | 把 `LLM_BASE_URL` / `LLM_MODEL_NAME` 指到别的 OpenAI 兼容供应商 |
| 抽取质量不理想 | 在本体生成阶段调整实体/关系类型 |
| 模拟太慢 | 调小 `.env` 里的 `OASIS_DEFAULT_MAX_ROUNDS`（默认 10） |
| 模拟卡在 "running" | 检查是否有残留的 runner 进程；现在运行器会检测停滞，并在启动时回收孤儿进程 |
| 实体数看着被封顶了 | 超过读取上限的图谱会被截断，界面现在会明确提示 |
| 想重置图谱 | 在第 1 步用「删除并重建」，或直接删掉 `backend/uploads/graphs.db` |

## 🧪 测试

```bash
cd backend
uv run pytest tests -q                              # 92 项，离线
uv run python scripts/run_benchmark.py verify       # 39 项检查，桩驱动
uv run python scripts/test_multi_format.py          # 11 例格式
```

```bash
cd frontend
node scripts/check-markdown.mjs                     # 18 例
```

以上全部不需要联网，也不会消耗 LLM 调用。

## 📚 文档

- [架构与运行逻辑](./docs/ARCHITECTURE.md) —— 流水线、存储布局、进程模型
- [操作手册](./docs/MANUAL.md) —— 安装、配置、跑完一次完整推演、排查

## 📄 致谢

- 模拟引擎：**[OASIS](https://github.com/camel-ai/oasis)**，感谢 CAMEL-AI 团队的开源贡献
- 上游项目：**[MiroFish](https://github.com/666ghj/MiroFish)**

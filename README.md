# 智能宠物健康管家 🐾

> 用 **LangChain 构建 ReAct Agent**，基于 **SQLite** 宠物健康数据，提供档案管理、健康记录、到期提醒，并通过自然语言对话完成智能问答与自动生成健康报告的 Web 应用。

- **产品形态**：Web 单页应用（HTML/CSS/JS 前端 + Python FastAPI 后端）
- **设计语言**：Notion 暖色极简 · 陶土橙强调（#C2703D）· Fraunces 衬线标题 · 亮暗双模式
- **核心闭环**：档案管理 → 健康记录 → 到期提醒 → AI 问答 → 自动健康报告

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | HTML + CSS + JS 单页应用（无框架，体重趋势图为手绘 SVG） |
| 后端 | Python 3 + FastAPI + Uvicorn |
| AI | LangChain ReAct Agent + 通义千问（DashScope, qwen-plus） |
| 数据 | SQLite 本地持久化（首次运行自动建库并注入示例数据） |
| Key | `.env`（`DASHSCOPE_API_KEY`，不进 git）· 无 Key 自动降级"示例回答"模式 |

## 快速开始

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 可选：配置真实大模型
cp env.example .env       # 编辑 .env 填入 DASHSCOPE_API_KEY=sk-xxx

python main.py            # 启动 http://127.0.0.1:8000
```

浏览器打开 **http://127.0.0.1:8000** 即可使用（后端同时托管前端页面）。

## 功能一览

- **仪表盘**：宠物总数 / 健康记录数 / 临期项目数（数字滚动动画）、到期提醒直达宠物、最近动态、AI 建议问题入口
- **宠物档案**：卡片网格 + 搜索（名字/品种）+ 类型/状态筛选 + 排序；新增/编辑/删除（确认提示）；状态徽章与临期角标
- **健康记录**：疫苗/体检/驱虫/喂药/就诊五类；下次日期驱动提醒；宠物详情含时间线与体重趋势 SVG 图
- **到期提醒**：有"下次日期"的记录，≤7 天标"临期"、已过标"逾期"；示例数据采用相对日期，任何时候演示均有真实临期项
- **AI 助手（LangChain Agent）**：
  - 五个工具：`query_pet` / `query_health_records` / `get_reminders` / `analyze_health` / `generate_report`
  - 有 Key：通义千问驱动 ReAct 循环，自主决定查库、多轮调用后作答
  - 无 Key：降级"示例回答"模式，解析关键词调用同一套工具、用数据库真实数据拼答案，全流程仍可演示
  - 报告答案支持 Markdown 渲染 + 复制 + 下载 `.md`

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/pets` | 列表（含最新体重/记录数/临期项）/ 新增 |
| PUT/DELETE | `/api/pets/{id}` | 编辑 / 删除（级联删记录） |
| GET/POST | `/api/pets/{id}/records` | 健康记录列表 / 新增（带体重则同步体重表） |
| DELETE | `/api/records/{id}` | 删除记录 |
| GET | `/api/pets/{id}/weights` | 体重历史 |
| GET | `/api/reminders` · `/api/stats` | 临期/逾期列表 · 仪表盘统计 |
| POST | `/api/chat` | `{message}` → `{reply, mode: agent|example}` |
| GET | `/api/agent/status` | 当前 AI 运行模式探测 |
| GET | `/` | 前端单页 |

## 演示流程（面向评审）

1. 启动后端 → 打开页面，看到仪表盘统计与到期提醒（逾期红点脉冲、临期黄色标记）。
2. 宠物库：搜索"柯基"、筛选"猫"，新增一只宠物 → 卡片出现并带状态徽章。
3. 进入宠物详情：查看健康时间线（五类记录色点区分）与体重趋势折线图。
4. 新增一条疫苗记录并填"下次日期" → 回到仪表盘看到新临期提醒。
5. 打开 AI 助手，点击建议问题或直接提问（如"可乐接下来要打什么疫苗？"）。
6. 请求"生成布丁的健康报告" → Markdown 报告渲染 + 复制 / 下载 `.md`。
7. 右上角一键切换亮/暗模式（偏好自动记忆）。

> 注：未配置 `DASHSCOPE_API_KEY` 时，AI 回答自动走"示例回答"模式（数据库真实数据拼装），演示链路不中断；配置 Key 后自动升级为 LangChain ReAct Agent。涉及医疗判断的回答均附"以兽医意见为准"提示。

## 项目结构

```
smart-pet-health/
├── backend/
│   ├── main.py            # FastAPI 路由 + 静态托管
│   ├── db.py              # SQLite 建库/CRUD/示例数据/提醒计算
│   ├── tools.py           # 五个 Agent 工具（LangChain 与降级模式共用）
│   ├── agent.py           # LangChain ReAct Agent + 无Key ExampleAgent
│   ├── requirements.txt
│   └── env.example        # 密钥模板（复制为 .env；.env 不进 git）
├── web/
│   └── index.html         # 单页前端（视图/样式/微交互全内含）
├── dev/
│   └── smoke.test.js      # jsdom 端到端冒烟测试（23 项断言）
└── README.md
```

## 测试

```bash
# 后端启动后，在装有 Node 的机器上：
npm install jsdom
node dev/smoke.test.js     # 23/23 通过：渲染、筛选、时间线、体重图、CRUD闭环、AI问答、主题
```

## 开发阶段（每阶段 git 提交）

- [x] P0 初始化目录结构 + git init + README 骨架
- [x] P1 SQLite（建库+示例）+ FastAPI 骨架（CRUD 全接口验证）
- [x] P2 LangChain Agent + 五个工具 + 无Key容错 + /api/chat
- [x] P3 前端单页（仪表盘/宠物/详情/提醒/AI 对话）
- [x] P4 前后端联调（23 项冒烟）+ 响应式 + 微交互
- [x] P5 冷启动全流程测试 + README 完善

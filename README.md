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
| AI | LangChain Agent + **DeepSeek**（OpenAI 兼容接口，原生 function calling）· 兼容通义千问（DashScope） |
| 数据 | SQLite 本地持久化（首次运行自动建库并注入示例数据） |
| Key | `.env`（`DEEPSEEK_API_KEY` 或 `DASHSCOPE_API_KEY`，不进 git）· 无 Key 自动降级"示例回答"模式 |

## 快速开始

**最简单（答辩演示推荐）**：双击项目根目录的 **`启动.bat`** —— 自动启动本地服务，3 秒后浏览器自动打开欢迎页。**演示期间不要关那个黑色命令行窗口**（它就是服务器），讲完关掉即可。

命令行方式（等效）：

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 可选：配置真实大模型（不配置也能完整演示，自动走"示例回答"模式）
cp env.example .env       # 编辑 .env 填入 DEEPSEEK_API_KEY=sk-xxx

python main.py            # 启动 http://127.0.0.1:8000
```

浏览器打开 **http://127.0.0.1:8000** —— 先看到欢迎页，点「开始使用」播放过渡动画后进入应用（直接访问 `/app` 可跳过欢迎页）。

## 功能一览

- **仪表盘**：宠物总数 / 健康记录数 / 临期项目数（数字滚动动画）、到期提醒直达宠物、最近动态、AI 建议问题入口
- **宠物档案**：卡片网格 + 搜索（名字/品种）+ 类型/状态筛选 + 排序；新增/编辑/删除（确认提示）；状态徽章与临期角标
- **健康记录**：疫苗/体检/驱虫/喂药/就诊五类；下次日期驱动提醒；宠物详情含时间线与体重趋势 SVG 图
- **到期提醒**：有"下次日期"的记录，≤7 天标"临期"、已过标"逾期"；示例数据采用相对日期，任何时候演示均有真实临期项
- **回忆集**（增量模块）：情感向回忆时间轴——屏 1 全局时间轴（年份分组/宠物筛选/缩略图/大图预览），屏 2 单宠回忆墙（陪伴天数）；支持新增/编辑/删除，图片前端压缩为 base64 存库；AI 助手可通过 `query_memories` 工具回忆故事
- **物种档案**：按物种（狗/猫/鸟/鱼/其他）明确该做与不该做的事——详情页「护理要点」页签、新增记录按物种过滤类型（鱼类无疫苗、无"腹泻"）并附常见病症快捷填入、后端校验兜底；AI 通过 `get_care_guide` 遵守物种边界回答
- **AI 助手（LangChain Agent）**：
  - 七个工具：`query_pet` / `query_health_records` / `get_reminders` / `analyze_health` / `generate_report` / `query_memories`（回忆集联动）/ `get_care_guide`（物种护理规范）
  - **自然语言建档**：对 AI 说"帮我记一下：可乐今天打了狂犬疫苗，明年这时候再打"，Agent 解析相对日期起草记录，前端确认卡片一键入库（AI 提议、人确认，物种校验兜底）
  - **对话会话**：历史会话自动保存，AI 助手左侧列表随时回看、点击继续聊（支持"那它的体重呢？"式追问）；单个会话可删除，「新对话」另起炉灶不删历史
  - **仪表盘 AI 今日简报**：按天自动生成健康简报（临期/逾期逐条建议+体重提示），数据签名过期自动后台刷新，无 Key 降级规则拼接
  - 有 Key：DeepSeek 原生 function calling 驱动工具调用循环，自主决定查库、多轮调用后作答（备选：通义千问文本 ReAct）
  - 无 Key：降级"示例回答"模式，解析关键词调用同一套工具、用数据库真实数据拼答案，全流程仍可演示
  - 报告答案支持 Markdown 渲染 + 复制 + 下载 `.md`

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/pets` | 列表（含最新体重/记录数/临期项）/ 新增 |
| PUT/DELETE | `/api/pets/{id}` | 编辑 / 删除（级联删记录） |
| GET/POST | `/api/pets/{id}/records` | 健康记录列表 / 新增（按物种校验类型；带体重则同步体重表） |
| PUT/DELETE | `/api/records/{id}` | 编辑（同样按物种校验）/ 删除记录 |
| GET/POST | `/api/pets/{id}/weights` | 体重历史 / 独立体重补录（同步宠物当前体重） |
| GET | `/api/species` | 物种档案（适用记录类型/常见疾病/该做与不该做） |
| GET/POST | `/api/memories` | 回忆列表（`?pet_id=` 过滤）/ 新增（含 base64 图片） |
| PUT/DELETE | `/api/memories/{id}` | 编辑 / 删除回忆 |
| GET | `/api/reminders` · `/api/stats` | 临期/逾期列表 · 仪表盘统计 |
| POST | `/api/chat` | `{message, session_id?}` → `{reply, mode, draft?, session_id}`；带会话内多轮记忆，返回 AI 起草的记录草稿 |
| GET | `/api/chat/sessions` · `/api/chat/history?session_id=` | 历史会话列表 / 某会话的消息记录 |
| DELETE | `/api/chat/sessions/{id}` | 删除单个历史会话及其全部消息 |
| GET/POST | `/api/briefing` | 今日 AI 健康简报（按天+数据签名缓存）/ 手动重新生成 |
| GET | `/api/agent/status` | 当前 AI 运行模式探测 |
| GET | `/` | 欢迎页（入口） |
| GET | `/app` | 主应用单页 |

## 演示流程（面向评审）

1. 启动后端 → 打开 http://127.0.0.1:8000 看到**欢迎页**（亮暗可切）→ 点「开始使用」→ 播放「欢迎来到毛孩子之家」过渡动画 → 自动进入**仪表盘**，看到统计与到期提醒（逾期红点脉冲、临期黄色标记）。
2. 宠物库：搜索"柯基"、筛选"猫"，新增一只宠物 → 卡片出现并带状态徽章。
3. 进入宠物详情：查看健康时间线（五类记录色点区分）与体重趋势折线图。
4. 新增一条疫苗记录并填"下次日期" → 回到仪表盘看到新临期提醒。
5. 打开 AI 助手，点击建议问题或直接提问（如"可乐接下来要打什么疫苗？"）。
6. 请求"生成布丁的健康报告" → Markdown 报告渲染 + 复制 / 下载 `.md`。
7. 右上角一键切换亮/暗模式（偏好自动记忆）。

> 注：未配置任何 Key 时，AI 回答自动走"示例回答"模式（数据库真实数据拼装），演示链路不中断；配置 `DEEPSEEK_API_KEY`（推荐）或 `DASHSCOPE_API_KEY` 后自动升级为 LangChain 工具调用 Agent。涉及医疗判断的回答均附"以兽医意见为准"提示。

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
│   ├── welcome.html       # 欢迎页（入口，含过渡动画，无外部依赖）
│   └── index.html         # 单页前端（视图/样式/微交互全内含）
├── dev/
│   └── smoke.test.js      # jsdom 端到端冒烟测试（46 项断言）
└── README.md
```

## 测试

```bash
# 后端启动后，在装有 Node 的机器上：
npm install jsdom
node dev/smoke.test.js     # 46/46 通过：渲染、筛选、时间线、体重图、CRUD闭环、AI问答、主题
```

## 开发阶段（每阶段 git 提交）

- [x] P0 初始化目录结构 + git init + README 骨架
- [x] P1 SQLite（建库+示例）+ FastAPI 骨架（CRUD 全接口验证）
- [x] P2 LangChain Agent + 五个工具 + 无Key容错 + /api/chat
- [x] P3 前端单页（仪表盘/宠物/详情/提醒/AI 对话）
- [x] P4 前后端联调（23 项冒烟）+ 响应式 + 微交互
- [x] P5 冷启动全流程测试 + README 完善

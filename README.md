# 智能宠物健康管家

> 用 LangChain 构建 AI Agent，基于 SQLite 宠物健康数据，提供档案管理、健康记录、到期提醒与自然语言智能问答/健康报告生成。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | HTML + CSS + JS 单页应用（Notion 暖色极简 · 亮暗双模式） |
| 后端 | Python + FastAPI |
| AI | LangChain ReAct Agent + 通义千问（DashScope） |
| 数据 | SQLite 本地持久化 |

## 功能闭环

档案管理 → 健康记录 → 到期提醒 → AI 问答 → 自动生成健康报告

## 运行方式

```bash
cd backend
python -m venv .venv
# 激活虚拟环境后：
pip install -r requirements.txt
# 可选：将 DASHSCOPE_API_KEY 写入 .env（未配置时自动进入"示例回答"模式）
python main.py            # 启动 http://127.0.0.1:8000
```

浏览器打开 `http://127.0.0.1:8000` 即可使用。

## 项目结构

```
smart-pet-health/
├── backend/
│   ├── main.py         # FastAPI 路由 + 静态托管
│   ├── db.py           # SQLite 建库/CRUD/示例数据
│   ├── tools.py        # 五个 Agent 工具
│   ├── agent.py        # LangChain Agent + 无Key降级
│   ├── requirements.txt
│   └── env.example     # 密钥模板（真实 .env 不进 git）
├── web/
│   └── index.html      # 单页前端
└── README.md
```

## 开发阶段

- [x] P0 项目初始化
- [ ] P1 数据库 + API 骨架
- [ ] P2 LangChain Agent + 工具 + 无Key容错
- [ ] P3 前端页面
- [ ] P4 联调 + 响应式 + 微交互
- [ ] P5 全流程测试 + 文档完善

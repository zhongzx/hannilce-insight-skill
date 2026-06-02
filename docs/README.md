# 文档中心

> 所有项目文档统一存放于此，使用 Markdown 格式，可直接在 GitHub 网页阅读。

## 目录结构

```
docs/
├── 01-project/          # 项目文档
├── 02-implementation/  # 实施文档
└── 03-operations/      # 运维文档
```

---

## 01-project — 项目文档

| 文档 | 状态 | 说明 |
|------|------|------|
| [SPEC.md](./01-project/SPEC.md) | ✅ 已完成 | 项目需求与目标 |
| [ARCHITECTURE.md](./01-project/ARCHITECTURE.md) | 📋 待撰写 | 系统架构设计 |
| [ROADMAP.md](./01-project/ROADMAP.md) | 📋 待撰写 | 开发路线图 |
| [IMPLEMENTATION_PLAN.md](./01-project/IMPLEMENTATION_PLAN.md) | 📋 待撰写 | 实施方案 |
| [GLOSSARY.md](./01-project/GLOSSARY.md) | 📋 待撰写 | 术语表 |

---

## 02-implementation — 实施文档

| 文档 | 状态 | 说明 |
|------|------|------|
| [TOPIC_GENERATOR.md](./02-implementation/TOPIC_GENERATOR.md) | 📋 待撰写 | 话题生成模块设计 |
| [QUALITY_CONTROL.md](./02-implementation/QUALITY_CONTROL.md) | 📋 待撰写 | 质量控制机制 |
| [CONFIDENCE.md](./02-implementation/CONFIDENCE.md) | 📋 待撰写 | 置信度评估模型 |
| [INTERACTION_FLOW.md](./02-implementation/INTERACTION_FLOW.md) | 📋 待撰写 | 用户交互流程设计 |
| [REPORT_FORMAT.md](./02-implementation/REPORT_FORMAT.md) | 📋 待撰写 | 报告输出格式设计 |

---

## 03-operations — 运维文档

| 文档 | 状态 | 说明 |
|------|------|------|
| [DATABASE.md](./03-operations/DATABASE.md) | ✅ 已完成 | 数据库结构说明 |
| [TOPIC_POOL.md](./03-operations/TOPIC_POOL.md) | ✅ 已完成 | 内置话题池 |
| [SCRAPER_LOG.md](./03-operations/SCRAPER_LOG.md) | 📋 待撰写 | 采集任务运维记录 |

---

## 写作规范

- Markdown 格式，文件名全小写、中划线分隔
- 每个文档顶部包含：标题、状态标签（✅已完成 / 📋待撰写 / 🔄进行中）
- 核心决策和结论用表格或引用块突出，避免散落全文

---
name: hannilce-insight
label: 汉尼尔斯读心术
description: 基于多轮话题对话的 MBTI 性格分析技能（以 DESIGN.md 为准）。触发方式：/MBTI
triggers:
  - /MBTI
commands:
  - name: /MBTI
    description: 开始 MBTI 性格分析，用法：/MBTI <姓名>
handler:
  command: "cd ${SKILL_DIR} && PYTHONPATH=${SKILL_DIR} python3 mbti/insight_skill.py"
---

# 汉尼尔斯读心术（Hannilce Insight）

> 从《沉默的羔羊》的审讯室里汲取灵感——两个人隔着玻璃，互相拆解对方的防线。
> 优雅、克制、暗流涌动。
> **Hannilce** = Hannibal + Clarice，取首尾咬合，如同一个神秘的代号。

## 核心哲学

本技能不依赖问卷，而是通过**真实话题讨论**来"读"一个人。

- 用户对新闻事件的选择性关注 → 揭示价值观
- 用户对话题的展开方式 → 揭示思维方式
- 用户表达的立场与情感 → 揭示内外向与情感模式
- 用户做决策的节奏与理由 → 揭示判断偏好

整个过程自然得像一次深夜对话，汉尼拔式的引导，克拉丽斯式的投入。

---

## 触发规则

**触发词**：`/MBTI`（后跟空格或直接跟姓名）

```
示例：
/MBTI 张三
/MBTI 李四
/MBTI 王五
```

- 数据默认本地持久化到 SQLite：`~/.hermes/mbti/sessions.db`
- 话题由 LLM 动态生成，优先使用本地种子缓存作为锚点（`seed_data/scraped_topics.json`）
- 详细设计口径以 [DESIGN.md](../docs/01-project/DESIGN.md) 为准

---

## 输出报告

分析完成后，输出结构化报告：

```
【汉尼可儿读心术 · MBTI性格画像】

🔍 用户：张三
📊 分析置信度：85%

── 四维分析 ──
E/I ████████████░░░░░░░ 7:3 → 偏外向（ENTP倾向）
S/N ███████░░░░░░░░░░░░ 3:7 → 直觉型
T/F ██████████████░░░░░ 8:2 → 思考型
J/P ████████████░░░░░░░ 6:4 → 判断型

🎯 MBTI 类型推断：ENTJ

── 核心特质描述 ──
[基于对话内容生成的个性化性格描述]

💬 典型表达特征：
• [从对话中提取的说话风格特征]

📰 对新闻话题的偏好：
• [分析用户关注的话题类型，揭示价值观]
```

---

## 唤醒机制（跨Session继续）

用户重新发起 `/MBTI 张三` 时：

1. 查询数据库中张三的会话记录
2. 若状态为"封存"或"进行中"：
   - 方案B（推荐）：将历史对话摘要注入 system prompt，继承上下文
3. 继续未完成的话题或开启新一轮分析

---

## 依赖文件

- [DESIGN.md](../docs/01-project/DESIGN.md) — 设计冻结口径
- [ARCHITECTURE.md](../docs/01-project/ARCHITECTURE.md) — 架构口径（与 DESIGN 对齐）
- [IMPLEMENTATION_PLAN.md](../docs/01-project/IMPLEMENTATION_PLAN.md) — 实施方案（与 DESIGN 对齐）

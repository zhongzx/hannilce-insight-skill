# MBTI 系统 — 数据库表结构设计

> SQLite + WAL 模式
> 路径：`~/.aily/workspace/mbti/mbti.db`

---

## 表 1：`users` — 用户基础表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增ID |
| user_hash | TEXT | UNIQUE NOT NULL | 用户唯一标识，SHA256前8位 |
| name | TEXT | NOT NULL | 姓名 |
| gender | TEXT | NOT NULL | 性别（男/女/其他） |
| birthday | TEXT | NOT NULL | 出生日期（YYYY-MM-DD） |
| occupation | TEXT | NOT NULL | 职业 |
| created_at | TEXT | NOT NULL | 首次录入时间（ISO8601） |
| updated_at | TEXT | NOT NULL | 最后更新时间 |
| status | TEXT | DEFAULT 'active' | 状态：active / archived |

**索引**：`idx_user_hash` ON user_hash

---

## 表 2：`sessions` — 会话表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增ID |
| user_hash | TEXT | NOT NULL | 关联用户 |
| session_id | TEXT | UNIQUE NOT NULL | 当前会话ID |
| started_at | TEXT | NOT NULL | 会话开始时间 |
| ended_at | TEXT | | 会话结束时间 |
| topic_count | INTEGER | DEFAULT 0 | 本会话话题数 |
| quality_avg | REAL | DEFAULT 0.0 | 本会话平均质量分 |
| status | TEXT | DEFAULT 'active' | active / completed / archived |

**索引**：`idx_session_user` ON user_hash；`idx_session_status` ON status

---

## 表 3：`topics` — 话题记录表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增ID |
| user_hash | TEXT | NOT NULL | 关联用户 |
| session_id | TEXT | NOT NULL | 关联会话 |
| round | INTEGER | NOT NULL | 本会话第几轮 |
| topic | TEXT | NOT NULL | 话题原文 |
| topic_source | TEXT | NOT NULL | 来源：news / fallback / generated |
| topic_dimension | TEXT | | 维度标签：EI/SN/TF/JP |
| raw_response | TEXT | NOT NULL | 用户原始回答 |
| response_tokens | INTEGER | | 回复Token数 |
| quality_score | REAL | | 质量分（0-1） |
| topic_score | REAL | | 话题分（0-1） |
| created_at | TEXT | NOT NULL | 创建时间 |

**索引**：`idx_topic_user_session` ON (user_hash, session_id)

---

## 表 4：`scored_rounds` — 维度打分记录表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增ID |
| user_hash | TEXT | NOT NULL | 关联用户 |
| session_id | TEXT | NOT NULL | 关联会话 |
| round | INTEGER | NOT NULL | 对应话题轮次 |
| dimension | TEXT | NOT NULL | 维度：E/I/S/N/T/F/J/P |
| score | REAL | NOT NULL | 维度得分（0-1） |
| confidence | REAL | | 本轮置信度 |
| method | TEXT | | 打分方式：token / semantic |
| created_at | TEXT | NOT NULL | 创建时间 |

**索引**：`idx_score_user` ON user_hash；`idx_score_dim` ON dimension

---

## 表 5：`reports` — 报告表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增ID |
| user_hash | TEXT | UNIQUE NOT NULL | 关联用户（每用户仅一份最新报告） |
| mbti_type | TEXT | NOT NULL | 4字母结果，如 ENFP |
| radar_scores | TEXT | NOT NULL | JSON，5维度得分 |
| confidence | REAL | NOT NULL | 置信度（0-1） |
| topics_used | TEXT | | JSON，已用话题列表 |
| dimensions_covered | TEXT | | JSON，四维度覆盖情况 |
| generated_at | TEXT | NOT NULL | 生成时间 |
| version | INTEGER | DEFAULT 1 | 报告版本 |

---

## 初始化 SQL

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    gender TEXT NOT NULL,
    birthday TEXT NOT NULL,
    occupation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_user_hash ON users(user_hash);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT NOT NULL,
    session_id TEXT UNIQUE NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    topic_count INTEGER DEFAULT 0,
    quality_avg REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(user_hash);
CREATE INDEX IF NOT EXISTS idx_session_status ON sessions(status);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    topic TEXT NOT NULL,
    topic_source TEXT NOT NULL,
    topic_dimension TEXT,
    raw_response TEXT NOT NULL,
    response_tokens INTEGER,
    quality_score REAL,
    topic_score REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_user_session ON topics(user_hash, session_id);

CREATE TABLE IF NOT EXISTS scored_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    confidence REAL,
    method TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_score_user ON scored_rounds(user_hash);
CREATE INDEX IF NOT EXISTS idx_score_dim ON scored_rounds(dimension);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_hash TEXT UNIQUE NOT NULL,
    mbti_type TEXT NOT NULL,
    radar_scores TEXT NOT NULL,
    confidence REAL NOT NULL,
    topics_used TEXT,
    dimensions_covered TEXT,
    generated_at TEXT NOT NULL,
    version INTEGER DEFAULT 1
);
```

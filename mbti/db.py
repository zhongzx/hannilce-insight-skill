"""
数据库层：SQLite WAL 模式初始化 + 表结构管理

路径：/home/gem/.aily/workspace/mbti/sessions.db
模式：WAL（Write-Ahead Logging），支持并发读写
"""

import sqlite3
from datetime import timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent / "sessions.db"


# ---------------------------------------------------------------------------
# 连接工厂
# ---------------------------------------------------------------------------
def get_connection(foreign_keys: bool = True) -> sqlite3.Connection:
    """
    创建数据库连接，自动启用 WAL 模式。

    Args:
        foreign_keys: 是否启用外键约束（默认开启）

    Returns:
        sqlite3.Connection 对象
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "PRAGMA foreign_keys = ON" if foreign_keys else "PRAGMA foreign_keys = OFF"
    )
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 初始化：创建所有表
# ---------------------------------------------------------------------------
def init_db() -> None:
    """
    初始化数据库：创建所有表和索引。
    如果表已存在则跳过（CREATE TABLE IF NOT EXISTS）。
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 用户画像表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mbti_profiles (
            user_id        TEXT PRIMARY KEY,   -- 姓名+首次认证时间戳哈希
            name           TEXT,               -- 用户姓名（原始）
            final_type     TEXT,               -- 最终推断的 MBTI 类型（如 INFP）
            confidence     REAL DEFAULT 0.0,   -- 置信度 0.0~1.0
            dimensions     TEXT,               -- 四维打分 JSON {"EI":0.5,"SN":0.5,"TF":0.5,"JP":0.5}
            created_at     TEXT,
            updated_at     TEXT,
            archived       INTEGER DEFAULT 0   -- 0=活跃, 1=已封存
        )
    """)

    # 2. 对话日志表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        TEXT,
            topic          TEXT,               -- LLM 生成的话题
            user_response  TEXT,               -- 用户的原始回复
            dimension      TEXT,                -- 话题映射的 MBTI 维度
            timestamp      TEXT,
            FOREIGN KEY (user_id) REFERENCES mbti_profiles(user_id)
        )
    """)

    # 3. 质量评分日志表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quality_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        TEXT,
            token_score    REAL,               -- Token 层评分（回复长度/平均回复）
            semantic_score REAL,               -- 语义层评分（GPT 评估 0~1）
            confidence     REAL,                -- 综合置信度
            timestamp      TEXT,
            FOREIGN KEY (user_id) REFERENCES mbti_profiles(user_id)
        )
    """)

    # 4. 话题池表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_pool (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            topic          TEXT,
            dimension      TEXT,                -- E/I/S/N/T/F/J/P
            source         TEXT,                -- 内置/新闻抓取
            created_at     TEXT,
            expires_at     TEXT                 -- 新闻话题 24h 过期
        )
    """)

    # 5. 滑动窗口记录表（记录最近 N 轮的质量分，用于封存判断）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sliding_window (
            user_id        TEXT PRIMARY KEY,
            recent_scores  TEXT,               -- JSON 数组，存储最近 N 个 semantic_score
            window_size    INTEGER DEFAULT 5,
            last_updated   TEXT,
            FOREIGN KEY (user_id) REFERENCES mbti_profiles(user_id)
        )
    """)

    # ---------------------------------------------------------------------------
    # 索引
    # ---------------------------------------------------------------------------
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_profiles_archived ON mbti_profiles(archived)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_user_id ON conversation_logs(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON conversation_logs(timestamp)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_quality_user_id ON quality_logs(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_quality_timestamp ON quality_logs(timestamp)"
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 快捷查询
# ---------------------------------------------------------------------------


def profile_exists(user_id: str) -> bool:
    """检查用户画像是否存在。"""
    conn = get_connection()
    cursor = conn.execute("SELECT 1 FROM mbti_profiles WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_profile(user_id: str) -> dict | None:
    """获取用户画像，不存在返回 None。"""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM mbti_profiles WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def save_profile(user_id: str, name: str) -> None:
    """创建新用户画像（首次触发时调用）。"""
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO mbti_profiles (user_id, name, dimensions, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, name, '{"EI":0.5,"SN":0.5,"TF":0.5,"JP":0.5}', now, now),
    )
    conn.commit()
    conn.close()


def update_profile(
    user_id: str,
    final_type: str = None,
    confidence: float = None,
    dimensions: str = None,
    archived: int = None,
) -> None:
    """更新用户画像字段（仅更新传入的非 None 字段）。"""
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()

    fields, values = [], []
    if final_type is not None:
        fields.append("final_type = ?")
        values.append(final_type)
    if confidence is not None:
        fields.append("confidence = ?")
        values.append(confidence)
    if dimensions is not None:
        fields.append("dimensions = ?")
        values.append(dimensions)
    if archived is not None:
        fields.append("archived = ?")
        values.append(archived)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(user_id)

    conn = get_connection()
    conn.execute(
        f"UPDATE mbti_profiles SET {', '.join(fields)} WHERE user_id = ?", values
    )
    conn.commit()
    conn.close()


def log_conversation(
    user_id: str, topic: str, user_response: str, dimension: str
) -> None:
    """记录一轮对话。"""
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO conversation_logs (user_id, topic, user_response, dimension, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, topic, user_response, dimension, now),
    )
    conn.commit()
    conn.close()


def log_quality(
    user_id: str, token_score: float, semantic_score: float, confidence: float
) -> None:
    """记录一轮质量评分。"""
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO quality_logs (user_id, token_score, semantic_score, confidence, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, token_score, semantic_score, confidence, now),
    )
    conn.commit()
    conn.close()


def get_recent_quality_scores(user_id: str, limit: int = 5) -> list[float]:
    """获取最近 N 轮的质量分（用于滑动窗口）。"""
    conn = get_connection()
    cursor = conn.execute(
        """
        SELECT semantic_score FROM quality_logs
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [r["semantic_score"] for r in rows]


def get_conversation_history(user_id: str, limit: int = 20) -> list[dict]:
    """获取最近的对话历史。"""
    conn = get_connection()
    cursor = conn.execute(
        """
        SELECT topic, user_response, dimension, timestamp
        FROM conversation_logs
        WHERE user_id = ?
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_topic(
    topic: str, dimension: str, source: str, expires_at: str = None
) -> None:
    """插入或更新话题（去重）。"""
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO topic_pool (topic, dimension, source, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (topic, dimension, source, now, expires_at),
    )
    conn.commit()
    conn.close()


def get_valid_topics(dimension: str = None, limit: int = 10) -> list[dict]:
    """
    获取有效话题（未过期）。

    Args:
        dimension: 可选，限定维度
        limit: 返回数量

    Returns:
        话题列表
    """
    from datetime import datetime

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    if dimension:
        cursor = conn.execute(
            """
            SELECT * FROM topic_pool
            WHERE (expires_at IS NULL OR expires_at > ?)
              AND dimension = ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (now, dimension, limit),
        )
    else:
        cursor = conn.execute(
            """
            SELECT * FROM topic_pool
            WHERE expires_at IS NULL OR expires_at > ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (now, limit),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 初始化入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    print(f"✅ 数据库初始化完成: {DB_PATH}")

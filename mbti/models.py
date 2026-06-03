"""
数据模型层：Pydantic 模型定义

提供类型安全的 Python 对象，与 db.py 的 SQLite 记录互相转换。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class MBTIDimension(str, Enum):
    """MBTI 四维维度"""

    EI = "EI"  # 外向 / 内向
    SN = "SN"  # 实感 / 直觉
    TF = "TF"  # 思考 / 情感
    JP = "JP"  # 判断 / 知觉


class TopicSource(str, Enum):
    """话题来源"""

    BUILTIN = "builtin"  # 内置话题池
    NEWS = "news"  # 新闻抓取


# ---------------------------------------------------------------------------
# 哈希工具
# ---------------------------------------------------------------------------


def make_user_id(name: str, timestamp: str) -> str:
    """
    生成用户唯一标识。

    Args:
        name: 用户姓名
        timestamp: 首次认证时间戳（ISO 格式字符串）

    Returns:
        SHA256 哈希前16位
    """
    raw = f"{name}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# MBTIProfile
# ---------------------------------------------------------------------------


class Dimensions(BaseModel):
    """四维打分"""

    EI: float = Field(default=0.5, ge=0.0, le=1.0)
    SN: float = Field(default=0.5, ge=0.0, le=1.0)
    TF: float = Field(default=0.5, ge=0.0, le=1.0)
    JP: float = Field(default=0.5, ge=0.0, le=1.0)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> Dimensions:
        return cls.model_validate_json(raw)

    def update(self, dimension: MBTIDimension, score: float) -> None:
        """更新单个维度的分值（增量更新）。"""
        current = getattr(self, dimension.value)
        # 移动平均：旧值权重 0.7，新值权重 0.3
        setattr(self, dimension.value, round(current * 0.7 + score * 0.3, 4))

    def to_mbti_type(self) -> str:
        """将四维分值转换为 MBTI 类型字符串。"""
        e = "E" if self.EI > 0.5 else "I"
        s = "S" if self.SN > 0.5 else "N"
        t = "T" if self.TF > 0.5 else "F"
        j = "J" if self.JP > 0.5 else "P"
        return f"{e}{s}{t}{j}"

    @property
    def EI_letter(self) -> str:
        return "E" if self.EI > 0.5 else "I"

    @property
    def SN_letter(self) -> str:
        return "S" if self.SN > 0.5 else "N"

    @property
    def TF_letter(self) -> str:
        return "T" if self.TF > 0.5 else "F"

    @property
    def JP_letter(self) -> str:
        return "J" if self.JP > 0.5 else "P"


class MBTIProfile(BaseModel):
    """用户 MBTI 画像"""

    user_id: str
    name: str
    final_type: str | None = None
    confidence: float = 0.0
    dimensions: Dimensions = Field(default_factory=Dimensions)
    created_at: str
    updated_at: str
    archived: int = 0

    @classmethod
    def from_db_row(cls, row: dict) -> MBTIProfile:
        """从 SQLite 行字典构建模型。"""
        dimensions = Dimensions.from_json(row.get("dimensions", "{}"))
        return cls(
            user_id=row["user_id"],
            name=row["name"],
            final_type=row.get("final_type"),
            confidence=row.get("confidence", 0.0),
            dimensions=dimensions,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived=row.get("archived", 0),
        )

    def to_dict(self) -> dict:
        return self.model_dump()

    def export_json(self) -> str:
        """导出为 JSON（用于用户数据迁移）。"""
        return json.dumps(
            {
                "user_id": self.user_id,
                "name": self.name,
                "final_type": self.final_type,
                "confidence": self.confidence,
                "dimensions": self.dimensions.model_dump(),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "archived": self.archived,
            },
            ensure_ascii=False,
            indent=2,
        )

    def to_summary(self) -> dict:
        """返回摘要（用于 skill 输出和日志）。"""
        return {
            "user_id": self.user_id,
            "name": self.name,
            "mbti_type": self.final_type or "待推断",
            "confidence": self.confidence,
            "dimensions": {
                "EI": self.dimensions.EI,
                "SN": self.dimensions.SN,
                "TF": self.dimensions.TF,
                "JP": self.dimensions.JP,
                "EI_letter": self.dimensions.EI_letter,
                "SN_letter": self.dimensions.SN_letter,
                "TF_letter": self.dimensions.TF_letter,
                "JP_letter": self.dimensions.JP_letter,
            },
            "archived": bool(self.archived),
        }


# ---------------------------------------------------------------------------
# ConversationLog
# ---------------------------------------------------------------------------


class ConversationLog(BaseModel):
    """单轮对话记录"""

    id: int | None = None
    user_id: str
    topic: str
    user_response: str
    dimension: str  # 话题映射到的 MBTI 维度
    timestamp: str

    @classmethod
    def from_db_row(cls, row: dict) -> ConversationLog:
        return cls(
            id=row.get("id"),
            user_id=row["user_id"],
            topic=row["topic"],
            user_response=row["user_response"],
            dimension=row["dimension"],
            timestamp=row["timestamp"],
        )


# ---------------------------------------------------------------------------
# QualityLog
# ---------------------------------------------------------------------------


class QualityLog(BaseModel):
    """单轮质量评分记录"""

    id: int | None = None
    user_id: str
    token_score: float
    semantic_score: float
    confidence: float
    timestamp: str

    @classmethod
    def from_db_row(cls, row: dict) -> QualityLog:
        return cls(
            id=row.get("id"),
            user_id=row["user_id"],
            token_score=row["token_score"],
            semantic_score=row["semantic_score"],
            confidence=row["confidence"],
            timestamp=row["timestamp"],
        )

    @property
    def decline_from_prev(self) -> float | None:
        """
        计算与前一轮的下降幅度（供滑动窗口使用）。
        本字段由 QualityController 在查询时计算，不存储。
        """
        return None  # 实际上由调用方在组装列表时计算


# ---------------------------------------------------------------------------
# TopicPool
# ---------------------------------------------------------------------------


class TopicPool(BaseModel):
    """话题池条目"""

    id: int | None = None
    topic: str
    dimension: str  # E/I/S/N/T/F/J/P
    source: str  # builtin / news
    created_at: str
    expires_at: str | None = None

    @classmethod
    def from_db_row(cls, row: dict) -> TopicPool:
        return cls(
            id=row.get("id"),
            topic=row["topic"],
            dimension=row["dimension"],
            source=row["source"],
            created_at=row["created_at"],
            expires_at=row.get("expires_at"),
        )

    def is_expired(self) -> bool:
        """检查话题是否已过期。"""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc).isoformat() > self.expires_at


# ---------------------------------------------------------------------------
# SlidingWindow
# ---------------------------------------------------------------------------


class SlidingWindow(BaseModel):
    """滑动窗口状态（用于质量趋势监控）"""

    user_id: str
    recent_scores: list[float] = Field(default_factory=list)
    window_size: int = 5
    last_updated: str

    @classmethod
    def from_db_row(cls, row: dict) -> SlidingWindow:
        scores = json.loads(row.get("recent_scores", "[]"))
        return cls(
            user_id=row["user_id"],
            recent_scores=scores,
            window_size=row.get("window_size", 5),
            last_updated=row["last_updated"],
        )

    def push(self, score: float) -> None:
        """推入新分数，超出窗口大小时移除最旧的。"""
        self.recent_scores.append(score)
        if len(self.recent_scores) > self.window_size:
            self.recent_scores.pop(0)
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def get_decline_delta(self) -> float | None:
        """
        计算最近 window_size 轮的质量下降幅度。

        Returns:
            None: 数据不足，无法判断
            float: delta = avg(后半段) - avg(前半段)，负值表示下降
        """
        if len(self.recent_scores) < 3:
            return None
        mid = len(self.recent_scores) // 2
        first_half = (
            self.recent_scores[:mid]
            if mid > 0
            else self.recent_scores[:1]
        )
        second_half = self.recent_scores[mid:]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        return round(avg_second - avg_first, 4)

    def should_archive(self) -> tuple[bool, str]:
        """
        判断是否应触发封存。

        Returns:
            (should_archive, reason)
        """
        delta = self.get_decline_delta()
        if delta is None:
            return False, "数据不足"

        # 下降超过阈值，判断严重程度
        if delta <= -0.3:
            # 严重下降：2 轮即触发（需要 len >= 2）
            if len(self.recent_scores) >= 2:
                return True, f"严重下降 delta={delta}"
        elif delta <= -0.15:
            # 中度下降：需要 len >= 3
            if len(self.recent_scores) >= 3:
                return True, f"中度下降 delta={delta}"
        else:
            # 轻微下降：需要 len >= 5
            if len(self.recent_scores) >= 5:
                return True, f"轻微下降 delta={delta}"

        return False, f"未达阈值 delta={delta}"

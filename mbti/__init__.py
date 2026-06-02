"""
汉尼可儿读心术（MBTI Insight）—— Python Package

模块结构：
    db.py            数据层：SQLite WAL 初始化与表管理
    models.py        模型层：Pydantic 数据模型
"""

from mbti.db import init_db
from mbti.models import (
    ConversationLog,
    Dimensions,
    MBTIDimension,
    MBTIProfile,
    QualityLog,
    SlidingWindow,
    TopicPool,
    TopicSource,
    make_user_id,
)
from mbti.quality_controller import QualityController
from mbti.session_manager import SessionManager
from mbti.topic_generator import TopicGenerator

__all__ = [
    # 数据层
    "init_db",
    # 模型
    "MBTIProfile",
    "ConversationLog",
    "QualityLog",
    "TopicPool",
    "SlidingWindow",
    "Dimensions",
    "MBTIDimension",
    "TopicSource",
    # 工具
    "make_user_id",
    # 会话管理
    "SessionManager",
    # 话题生成
    "TopicGenerator",
    # 质量控制
    "QualityController",
]

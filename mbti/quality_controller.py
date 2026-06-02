"""
QualityController: 双层评分 + 滑动窗口 + 封存判断

职责：
    1. Token 层评分（回复长度、响应速度）
    2. 语义层评分（GPT 评估回复质量）
    3. 滑动窗口监控质量趋势
    4. 封存判断（连续下降触发封存）
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from mbti import db, models
from mbti.models import SlidingWindow

# ---------------------------------------------------------------------------
# 评分常量
# ---------------------------------------------------------------------------

# Token 层基准值（基于历史数据校准）
_TOKEN_BASELINE_MIN = 20  # 最低有效回复（字数）
_TOKEN_BASELINE_AVG = 80  # 平均回复水平
_TOKEN_BASELINE_GOOD = 200  # 优质回复

# 语义层默认值（无 LLM 时的保守估计）
_DEFAULT_SEMANTIC_SCORE = 0.6


# ---------------------------------------------------------------------------
# QualityController
# ---------------------------------------------------------------------------


class QualityController:
    """
    质量控制器。

    用法：
        qc = QualityController()
        result = qc.evaluate_round(
            user_id="abc123",
            topic="你更关注细节还是可能性？",
            user_response="我觉得两者都重要，但具体要看场景...",
        )
        # result: {"token_score", "semantic_score", "confidence", "should_archive", "archive_reason"}
    """

    def __init__(self):
        self._window: SlidingWindow | None = None
        self._current_user_id: str | None = None

    # -------------------------------------------------------------------------
    # 核心评分接口
    # -------------------------------------------------------------------------

    def evaluate_round(
        self,
        user_id: str,
        topic: str,
        user_response: str,
        response_time_seconds: float | None = None,
        llm_semantic_score: float | None = None,
    ) -> dict:
        """
        评估一轮对话质量。

        Args:
            user_id: 用户标识
            topic: 本轮话题
            user_response: 用户回复原文
            response_time_seconds: 响应时间（秒），可选
            llm_semantic_score: LLM 语义评分（0.0~1.0），可选；不提供则用默认值

        Returns:
            {
                "token_score": float,
                "semantic_score": float,
                "confidence": float,         # 综合置信度
                "should_archive": bool,
                "archive_reason": str,
            }
        """
        # Token 层评分
        token_score = self._score_token(
            response=user_response,
            response_time=response_time_seconds,
        )

        # 语义层评分
        semantic_score = (
            llm_semantic_score
            if llm_semantic_score is not None
            else _DEFAULT_SEMANTIC_SCORE
        )

        # 综合置信度：三因子模型
        confidence = self._compute_confidence(token_score, semantic_score)

        # 更新滑动窗口
        sw = self._load_or_create_window(user_id)
        sw.push(semantic_score)
        self._save_window(sw)

        # 封存判断
        should_archive, reason = sw.should_archive()

        return {
            "token_score": round(token_score, 4),
            "semantic_score": round(semantic_score, 4),
            "confidence": round(confidence, 4),
            "should_archive": should_archive,
            "archive_reason": reason,
        }

    # -------------------------------------------------------------------------
    # Token 层评分
    # -------------------------------------------------------------------------

    def _score_token(
        self,
        response: str,
        response_time: float | None,
    ) -> float:
        """
        Token 层评分：回复长度 + 响应速度。

        计算公式：
            score = 0.7 * length_score + 0.3 * time_score

        Args:
            response: 用户回复原文
            response_time: 响应时间（秒），可选

        Returns:
            0.0~1.0 的评分
        """
        import re

        # 字数（中文按字符计，英文按单词计）
        char_count = len(response)
        word_count = len(re.findall(r"\w+", response))
        effective_length = char_count + word_count * 0.5

        # 长度评分（0.0~1.0）
        if effective_length < _TOKEN_BASELINE_MIN:
            length_score = effective_length / _TOKEN_BASELINE_MIN * 0.3
        elif effective_length < _TOKEN_BASELINE_AVG:
            length_score = (
                0.3
                + (effective_length - _TOKEN_BASELINE_MIN)
                / (_TOKEN_BASELINE_AVG - _TOKEN_BASELINE_MIN)
                * 0.3
            )
        elif effective_length < _TOKEN_BASELINE_GOOD:
            length_score = (
                0.6
                + (effective_length - _TOKEN_BASELINE_AVG)
                / (_TOKEN_BASELINE_GOOD - _TOKEN_BASELINE_AVG)
                * 0.4
            )
        else:
            length_score = 1.0

        # 时间评分（越快回答表示越投入，0.5~1.0）
        time_score = 0.8  # 默认值
        if response_time is not None:
            if response_time < 5:
                time_score = 1.0
            elif response_time < 30:
                time_score = 0.9
            elif response_time < 120:
                time_score = 0.8
            elif response_time < 600:
                time_score = 0.6
            else:
                time_score = 0.4

        return 0.7 * length_score + 0.3 * time_score

    # -------------------------------------------------------------------------
    # 置信度计算（三因子模型）
    # -------------------------------------------------------------------------

    def _compute_confidence(
        self,
        token_score: float,
        semantic_score: float,
        round_count: int | None = None,
    ) -> float:
        """
        综合置信度：三因子模型。

        公式：
            confidence = (token_score * 0.3 + semantic_score * 0.5 + recency * 0.2)
        其中 recency = min(round_count * 0.05, 0.95)，对话越多越自信

        Args:
            token_score: Token 层评分
            semantic_score: 语义层评分
            round_count: 当前对话轮数，不提供则从历史推算

        Returns:
            0.0~1.0 的置信度
        """
        if round_count is None:
            # 从数据库估算（保守取最近 10 轮）
            round_count = 5

        recency = min(round_count * 0.05, 0.95)
        return token_score * 0.3 + semantic_score * 0.5 + recency * 0.2

    # -------------------------------------------------------------------------
    # 滑动窗口
    # -------------------------------------------------------------------------

    def _load_or_create_window(self, user_id: str) -> SlidingWindow:
        """加载或创建滑动窗口。"""
        conn = db.get_connection()
        cursor = conn.execute(
            "SELECT * FROM sliding_window WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return models.SlidingWindow.from_db_row(dict(row))
        else:
            now = datetime.now(timezone.utc).isoformat()
            return models.SlidingWindow(
                user_id=user_id,
                recent_scores=[],
                window_size=5,
                last_updated=now,
            )

    def _save_window(self, sw: SlidingWindow) -> None:
        """保存滑动窗口到数据库。"""
        conn = db.get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO sliding_window (user_id, recent_scores, window_size, last_updated)
            VALUES (?, ?, ?, ?)
            """,
            (sw.user_id, json.dumps(sw.recent_scores), sw.window_size, sw.last_updated),
        )
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # 批量评估（用于历史数据回溯）
    # -------------------------------------------------------------------------

    def backfill_quality_scores(self, user_id: str) -> int:
        """
        对历史对话进行质量回填（批量评分，不触发封存）。

        用于初始化时对已有对话历史做质量评分。

        Args:
            user_id: 用户标识

        Returns:
            回填的记录数
        """
        history = db.get_conversation_history(user_id, limit=100)
        count = 0

        for log in history:
            # 已有评分则跳过
            if not log.get("user_response"):
                continue
            result = self.evaluate_round(
                user_id=user_id,
                topic=log.get("topic", ""),
                user_response=log["user_response"],
            )
            # 只写质量日志，不更新封存状态（回填不触发封存）
            db.log_quality(
                user_id=user_id,
                token_score=result["token_score"],
                semantic_score=result["semantic_score"],
                confidence=result["confidence"],
            )
            count += 1

        return count

    # -------------------------------------------------------------------------
    # 状态查询
    # -------------------------------------------------------------------------

    def get_window_status(self, user_id: str) -> dict:
        """
        获取滑动窗口当前状态。

        Returns:
            {
                "recent_scores": list[float],
                "decline_delta": float | None,
                "should_archive": bool,
                "archive_reason": str,
            }
        """
        sw = self._load_or_create_window(user_id)
        should_archive, reason = sw.should_archive()
        return {
            "recent_scores": sw.recent_scores,
            "decline_delta": sw.get_decline_delta(),
            "should_archive": should_archive,
            "archive_reason": reason,
        }

    # -------------------------------------------------------------------------
    # LLM 语义评分 prompt（供外部调用方传给 LLM）
    # -------------------------------------------------------------------------

    @staticmethod
    def build_semantic_prompt(
        topic: str,
        user_response: str,
    ) -> str:
        """
        构建 LLM 语义评分 prompt。

        调用方将此 prompt 发送给 LLM，LLM 返回 JSON：
            {"score": 0.0~1.0, "reason": "简短理由"}

        Args:
            topic: 本轮话题
            user_response: 用户回复原文

        Returns:
            完整的 prompt 字符串
        """
        return f"""你是一个 MBTI 对话质量评估专家。请评估用户回复的质量。

## 本轮话题
{topic}

## 用户回复
{user_response}

## 评估标准（0.0~1.0）
- 1.0：回复深刻、具体、有自我洞察，涉及个人经历和情感细节
- 0.7：回复较完整，有一定个人色彩
- 0.5：回复泛泛而谈，没有具体例子
- 0.3：回复简短、敷衍、答非所问
- 0.0：几乎无有效内容

## 要求
返回 JSON 格式，不要其他内容：
{{"score": 0.0~1.0之间的数值, "reason": "一句话评分理由"}}
"""

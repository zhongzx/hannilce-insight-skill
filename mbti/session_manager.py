"""
SessionManager: 会话创建、查询与唤醒

核心职责：
    1. 用户标识：姓名 + 首次认证时间戳 → 唯一 user_id
    2. 会话创建：首次触发时初始化画像
    3. 会话查询：获取用户画像和对话历史
    4. 会话唤醒：将历史上下文压缩后注入 system prompt
"""

from __future__ import annotations

from mbti import db, models
from mbti.models import MBTIProfile, make_user_id

# ---------------------------------------------------------------------------
# 唤醒上下文压缩提示词
# ---------------------------------------------------------------------------
_WAKEUP_TEMPLATE = """你是一位敏锐、善于倾听的朋友，正在继续与用户的对话。

## 用户基础信息
- 姓名：{name}
- 首次认证时间：{first_auth}

## 最近对话历史（{count} 轮）
{history}

## 当前任务
自然延续对话，提出开放式、口语化的问题。避免重复已问过的问题。
如果用户刚才回答很短或明显敷衍，就换一个更轻松的新方向。"""


def _format_history(profile: MBTIProfile, history: list[dict]) -> str:
    """将对话历史格式化为可读文本。"""
    if not history:
        return "（尚无对话记录）"

    lines = []
    for i, log in enumerate(history[-10:], 1):  # 最多显示最近10轮
        lines.append(
            f"  {i}. 话题: {log['topic']}\n     用户回复: {log['user_response'][:120]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """
    会话管理器。

    用法：
        sm = SessionManager()
        ctx = sm.get_or_create(
            user_name="张三",
            timestamp_iso="2026-06-03T00:00:00Z",
        )
        # ctx 包含 profile / history / system_prompt
    """

    def __init__(self):
        pass

    def get_or_create(
        self,
        user_name: str,
        timestamp_iso: str,
        *,
        gender: str | None = None,
        birth_yyyymm: str | None = None,
        occupation: str | None = None,
    ) -> dict:
        """
        获取或创建用户会话。

        Args:
            user_name: 用户姓名
            timestamp_iso: 首次认证时间戳（ISO 格式字符串，用于生成 user_id）

        Returns:
            {
                "profile": MBTIProfile,
                "history": list[dict],          # 最近的对话历史
                "is_new": bool,                  # 是否为新建会话
            }
        """
        user_id = make_user_id(user_name, timestamp_iso)
        is_new = not db.profile_exists(user_id)

        if is_new:
            db.save_profile(
                user_id,
                user_name,
                gender=gender,
                birth_yyyymm=birth_yyyymm,
                occupation=occupation,
            )
            profile = models.MBTIProfile.from_db_row(db.get_profile(user_id))
        else:
            profile_row = db.get_profile(user_id)
            profile = models.MBTIProfile.from_db_row(profile_row)
            if gender or birth_yyyymm or occupation:
                db.update_profile(
                    user_id,
                    gender=gender,
                    birth_yyyymm=birth_yyyymm,
                    occupation=occupation,
                )
                profile_row = db.get_profile(user_id)
                profile = models.MBTIProfile.from_db_row(profile_row)

        history = db.get_conversation_history(user_id, limit=20)

        return {
            "profile": profile,
            "history": history,
            "is_new": is_new,
        }

    def build_wakeup_context(
        self,
        user_name: str,
        timestamp_iso: str,
    ) -> str:
        """
        构建唤醒上下文（压缩版 system prompt）。

        用于从新 Session 恢复时，将历史上下文注入 system prompt，
        让 LLM 继承之前的分析状态。

        Args:
            user_name: 用户姓名
            timestamp_iso: 首次认证时间戳

        Returns:
            格式化后的上下文字符串，供注入 system prompt 使用
        """
        ctx = self.get_or_create(user_name, timestamp_iso)
        profile = ctx["profile"]
        history = ctx["history"]

        history_text = _format_history(profile, history)

        return _WAKEUP_TEMPLATE.format(
            name=profile.name,
            first_auth=profile.created_at[:10],
            count=len(history),
            history=history_text,
        )

    def record_round(
        self,
        user_id: str,
        topic: str,
        user_response: str,
        dimension: str,
        token_score: float,
        semantic_score: float,
        confidence: float,
    ) -> None:
        """
        记录一轮完整的对话+评分。

        一次性写入 conversation_logs 和 quality_logs。

        Args:
            user_id: 用户标识
            topic: 本轮话题
            user_response: 用户回复原文
            dimension: 话题映射的 MBTI 维度
            token_score: Token 层评分
            semantic_score: 语义层评分
            confidence: 综合置信度
        """
        db.log_conversation(user_id, topic, user_response, dimension)
        db.log_quality(user_id, token_score, semantic_score, confidence)

    def update_dimensions(
        self,
        user_id: str,
        dimension: str,
        score: float,
        *,
        round_score: float | None = None,
        session_confidence: float | None = None,
    ) -> MBTIProfile:
        """
        更新用户画像中的某个维度分值。

        Args:
            user_id: 用户标识
            dimension: 维度名（如 "EI"、"SN"）
            score: 新的原始分值（0.0~1.0，将与旧值做移动平均）

        Returns:
            更新后的 MBTIProfile
        """
        profile_row = db.get_profile(user_id)
        profile = models.MBTIProfile.from_db_row(profile_row)

        dim_enum = models.MBTIDimension(dimension)
        profile.dimensions.update(dim_enum, score)
        if round_score is not None:
            profile.dimension_confidences.update(dim_enum, round_score)

        # 推断 MBTI 类型（每轮更新）
        inferred_type = profile.dimensions.to_mbti_type()

        # 更新置信度（对话轮数越多置信度越高，上限 0.95）
        history = db.get_conversation_history(user_id, limit=100)
        base_conf = min(0.1 + len(history) * 0.05, 0.95)
        profile_confidence = (
            session_confidence if session_confidence is not None else base_conf
        )

        db.update_profile(
            user_id,
            dimensions=profile.dimensions.to_json(),
            final_type=inferred_type,
            confidence=profile_confidence,
            dimension_confidences=profile.dimension_confidences.to_json(),
        )

        return models.MBTIProfile.from_db_row(db.get_profile(user_id))

    def update_from_dimension_signals(
        self,
        *,
        user_id: str,
        signals: dict[str, float],
        evidence_strength: float,
        session_confidence: float | None = None,
    ) -> MBTIProfile:
        profile_row = db.get_profile(user_id)
        profile = models.MBTIProfile.from_db_row(profile_row)

        evidence = max(0.0, min(1.0, float(evidence_strength)))
        for key in ("EI", "SN", "TF", "JP"):
            raw = signals.get(key, 0.0)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0

            value = max(-1.0, min(1.0, value))
            if abs(value) < 0.1:
                continue

            dim_enum = models.MBTIDimension(key)
            score_01 = max(0.0, min(1.0, 0.5 + value / 2.0))
            profile.dimensions.update(dim_enum, score_01)

            confidence_signal = max(0.0, min(1.0, abs(value) * evidence))
            profile.dimension_confidences.update(dim_enum, confidence_signal)

        inferred_type = profile.dimensions.to_mbti_type()

        history = db.get_conversation_history(user_id, limit=100)
        base_conf = min(0.1 + len(history) * 0.05, 0.95)
        profile_confidence = (
            session_confidence if session_confidence is not None else base_conf
        )

        db.update_profile(
            user_id,
            dimensions=profile.dimensions.to_json(),
            final_type=inferred_type,
            confidence=profile_confidence,
            dimension_confidences=profile.dimension_confidences.to_json(),
        )

        return models.MBTIProfile.from_db_row(db.get_profile(user_id))

    def nudge_dimension_confidences(
        self,
        *,
        user_id: str,
        delta: float,
        dimensions: tuple[str, ...] = ("EI", "SN", "TF", "JP"),
    ) -> MBTIProfile:
        profile_row = db.get_profile(user_id)
        profile = models.MBTIProfile.from_db_row(profile_row)

        for key in dimensions:
            try:
                dim_enum = models.MBTIDimension(key)
            except ValueError:
                continue

            current = getattr(profile.dimension_confidences, dim_enum.value)
            target = max(0.0, min(1.0, float(current) + float(delta)))
            profile.dimension_confidences.update(dim_enum, target)

        db.update_profile(
            user_id,
            dimension_confidences=profile.dimension_confidences.to_json(),
        )
        return models.MBTIProfile.from_db_row(db.get_profile(user_id))

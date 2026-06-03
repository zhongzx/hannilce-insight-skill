"""
MBTI Insight Skill — 主入口模块

触发词：/MBTI 姓名

流程：
    1. 解析触发词，获取用户姓名
    2. 初始化/恢复会话（SessionManager）
    3. 获取下一个话题（TopicGenerator）
    4. 用户回复后，评分（QualityController）
    5. 更新画像维度（SessionManager）
    6. 循环直到达到结束条件
    7. 输出 MBTI 分析报告
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime

if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mbti import db
from mbti.models import MBTIProfile, make_user_id
from mbti.quality_controller import QualityController
from mbti.session_manager import SessionManager
from mbti.topic_generator_v2 import TopicGeneratorV2
from openrouter_client import (
    call_chat_completion,
    load_openrouter_settings,
)

# ---------------------------------------------------------------------------
# 触发词正则
# ---------------------------------------------------------------------------
TRIGGER_PATTERN = re.compile(r"^\s*/MBTI\s+(.+?)\s*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# 报告输出模板
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """
# MBTI 性格分析报告

## {name} 的人格画像

**分析类型：{mbti_type}**（{type_fullname}）

置信度：{confidence:.0%} | 分析轮数：{round_count} 轮

---

## 四维分析

| 维度 | 取向 | 倾向强度 | 说明 |
|------|------|----------|------|
| E ↔ I | {ei_label} | {ei_bar} {ei_pct:.0%} | {ei_desc} |
| S ↔ N | {sn_label} | {sn_bar} {sn_pct:.0%} | {sn_desc} |
| T ↔ F | {tf_label} | {tf_bar} {tf_pct:.0%} | {tf_desc} |
| J ↔ P | {jp_label} | {jp_bar} {jp_pct:.0%} | {jp_desc} |

---

## 人格特征

{character_traits}

---

## 职业倾向

{career_tendencies}

---

## 关系与沟通

{relationship_style}

---

## 成长建议

{growth_suggestions}

---

*本报告由 AI 基于对话分析生成，仅供参考。*
"""

# ---------------------------------------------------------------------------
# 维度说明映射
# ---------------------------------------------------------------------------

_DIMENSION_INFO = {
    "EI": {
        "E": {
            "label": "外向 (E)",
            "bar": "🔵",
            "desc": "你从外部世界和社交互动中获得能量，善于表达，喜欢与他人交流。",
        },
        "I": {
            "label": "内向 (I)",
            "bar": "🟡",
            "desc": "你从独处和内心思考中获得能量，倾向于深度对话而非广泛社交。",
        },
    },
    "SN": {
        "S": {
            "label": "实感 (S)",
            "bar": "🟢",
            "desc": "你注重实际细节和具体信息，信任可验证的事实和经验。",
        },
        "N": {
            "label": "直觉 (N)",
            "bar": "🟣",
            "desc": "你关注可能性和整体模式，善于发现联系和预见趋势。",
        },
    },
    "TF": {
        "T": {
            "label": "思考 (T)",
            "bar": "⚪",
            "desc": "你基于逻辑和客观分析做决定，注重因果关系和一致性。",
        },
        "F": {
            "label": "情感 (F)",
            "bar": "🔴",
            "desc": "你考虑决策对他人的影响，注重和谐与个人价值观。",
        },
    },
    "JP": {
        "J": {
            "label": "判断 (J)",
            "bar": "🟤",
            "desc": "你喜欢有计划、有组织的生活方式，倾向于提前安排。",
        },
        "P": {
            "label": "知觉 (P)",
            "bar": "⚫",
            "desc": "你偏好灵活、开放的方式，善于适应变化和即兴发挥。",
        },
    },
}


def _fallback_report_sections(profile: MBTIProfile) -> dict[str, str]:
    dims = profile.dimensions
    traits: list[str] = []
    if dims.EI < 0.45:
        traits.append("- 更倾向先自己消化，想清楚再表达")
    elif dims.EI > 0.55:
        traits.append("- 在互动中更容易打开思路，表达更顺")
    if dims.SN < 0.45:
        traits.append("- 更关注可能性与整体脉络，喜欢从意义出发")
    elif dims.SN > 0.55:
        traits.append("- 更关注事实与细节，偏好可落地的推进方式")
    if dims.TF < 0.45:
        traits.append("- 更容易先感受人和关系，再做判断")
    elif dims.TF > 0.55:
        traits.append("- 更容易先看逻辑与原则，再决定取舍")
    if dims.JP < 0.45:
        traits.append("- 更偏好留弹性，边走边调整")
    elif dims.JP > 0.55:
        traits.append("- 更偏好提前规划，把事情理顺")

    character_traits = "\n".join(traits) if traits else "- 信息不足，需要更多对话线索"
    return {
        "type_fullname": "（简版）",
        "character_traits": character_traits,
        "career_tendencies": ("- 如需更详细内容，请开启 OpenRouter 或继续对话补充线索"),
        "relationship_style": (
            "- 如需更详细内容，请开启 OpenRouter 或继续对话补充线索"
        ),
        "growth_suggestions": (
            "- 如需更详细内容，请开启 OpenRouter 或继续对话补充线索"
        ),
    }


def _render_report(profile: MBTIProfile, round_count: int) -> str:
    """渲染 MBTI 分析报告。"""
    mbti_type = profile.final_type or profile.dimensions.to_mbti_type()
    settings = load_openrouter_settings()
    if settings:
        history = db.get_conversation_history(profile.user_id, limit=50)
        history_lines = "\n".join(
            (f"- Q: {item.get('topic', '')}\n  A: {item.get('user_response', '')}")
            for item in history
        )
        prompt = (
            "你是一个 MBTI 性格分析师。请基于用户的多轮对话记录与当前画像，"
            "输出一份结构化、可读性强的中文 Markdown 报告。\n\n"
            "要求：\n"
            "1) 报告必须包含：四维分析、类型推断与置信度、人格特征、职业倾向、"
            "关系与沟通、成长建议\n"
            "2) 用具体措辞引用对话中的线索（不要捏造不存在的细节）\n"
            "3) 如果信息不足，明确说明需要补充什么信息\n"
            "4) 维度名称必须严格使用：EI(外向E/内向I)、SN(实感S/直觉N)、"
            "TF(思考T/情感F)、JP(判断J/知觉P)，不要写错维度对\n"
            f"5) 最终类型必须使用：{mbti_type}（以画像为准），"
            "如证据存在冲突，只说明冲突点，不要擅自改类型\n"
            "6) 全文用“你”称呼，不要用“您”\n"
            "7) 只输出 Markdown，不要输出代码块外的解释\n\n"
            f"用户：{profile.name}\n"
            f"轮数：{round_count}\n"
            "当前画像摘要："
            f"{json.dumps(profile.to_summary(), ensure_ascii=False)}\n\n"
            "对话记录：\n"
            f"{history_lines or '（无对话记录）'}\n"
        )
        content = call_chat_completion(
            settings=settings,
            messages=[
                {
                    "role": "system",
                    "content": "你擅长根据对话证据做 MBTI 分析并输出中文报告。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        if content:
            return content

    sections = _fallback_report_sections(profile)

    def dim_info(dim_name: str, letter: str) -> tuple[str, str, str]:
        info = _DIMENSION_INFO.get(dim_name, {}).get(letter, {})
        bar = info.get("bar", "⚪")
        desc = info.get("desc", "")
        label = info.get("label", letter)
        return label, bar, desc

    dims = profile.dimensions
    ei_label, ei_bar, ei_desc = dim_info("EI", dims.EI_letter)
    sn_label, sn_bar, sn_desc = dim_info("SN", dims.SN_letter)
    tf_label, tf_bar, tf_desc = dim_info("TF", dims.TF_letter)
    jp_label, jp_bar, jp_desc = dim_info("JP", dims.JP_letter)

    return _REPORT_TEMPLATE.format(
        name=profile.name,
        mbti_type=mbti_type,
        type_fullname=sections["type_fullname"],
        confidence=profile.confidence or 0,
        round_count=round_count,
        ei_label=ei_label,
        ei_bar=ei_bar,
        ei_pct=dims.EI,
        ei_desc=ei_desc,
        sn_label=sn_label,
        sn_bar=sn_bar,
        sn_pct=dims.SN,
        sn_desc=sn_desc,
        tf_label=tf_label,
        tf_bar=tf_bar,
        tf_pct=dims.TF,
        tf_desc=tf_desc,
        jp_label=jp_label,
        jp_bar=jp_bar,
        jp_pct=dims.JP,
        jp_desc=jp_desc,
        character_traits=sections["character_traits"],
        career_tendencies=sections["career_tendencies"],
        relationship_style=sections["relationship_style"],
        growth_suggestions=sections["growth_suggestions"],
    )


# ---------------------------------------------------------------------------
# InsightSkill 主入口
# ---------------------------------------------------------------------------


class InsightSkill:
    """
    MBTI 分析主入口。

    用法（供外部 skill 系统调用）：

        skill = InsightSkill()
        result = skill.handle_trigger(
            user_name="张三",
            timestamp_iso="2026-06-03T00:00:00+08:00",
        )
        # result: {"type": "next_topic", "topic": "...", "context": "..."}
        #
        # 用户回复后：
        result = skill.handle_response(
            user_name="张三",
            timestamp_iso="2026-06-03T00:00:00+08:00",
            topic="...",
            user_response="...",
        )
        # result: {"type": "next_topic", "topic": "...", "profile": {...}}
        # 或
        # result: {"type": "report", "content": "..."}
    """

    def __init__(self):
        self._sm: SessionManager | None = None
        self._tg: TopicGeneratorV2 | None = None
        self._qc: QualityController | None = None
        self._current_topic: str | None = None
        self._current_dimension: str | None = None
        self._last_summary: str | None = None
        self._pending_profile_field: str | None = None
        self._skipped_profile_fields: set[str] = set()
        self._pending_birth_confirmation: str | None = None
        self._awaiting_summary_feedback: bool = False
        self._last_user_id: str | None = None

    # -------------------------------------------------------------------------
    # 公共接口
    # -------------------------------------------------------------------------

    def init(self) -> None:
        """初始化子模块。"""
        db.init_db()
        self._sm = SessionManager()
        self._tg = TopicGeneratorV2()
        self._qc = QualityController()

    def handle_trigger(
        self,
        user_name: str,
        timestamp_iso: str,
        *,
        gender: str | None = None,
        birth_yyyymm: str | None = None,
        occupation: str | None = None,
    ) -> dict:
        """
        处理 /MBTI 触发。

        Args:
            user_name: 用户姓名
            timestamp_iso: 首次触发时间（ISO 格式）

        Returns:
            {
                "type": "next_topic",
                "topic": str,           # 下一个话题
                "dimension": str,        # 话题对应的维度
                "is_new": bool,         # 是否为新会话
                "profile": dict,        # 当前画像摘要
                "wakeup_context": str,  # 唤醒上下文（仅 is_new=False）
            }
        """
        self.init()

        # 获取/创建会话
        ctx = self._sm.get_or_create(
            user_name,
            timestamp_iso,
            gender=gender,
            birth_yyyymm=birth_yyyymm,
            occupation=occupation,
        )
        profile: MBTIProfile = ctx["profile"]
        is_new = ctx["is_new"]
        history = db.get_conversation_history(profile.user_id, limit=5)

        if not history:
            next_field = self._next_profile_field_to_collect(profile)
            if next_field is not None:
                topic = self._build_profile_question(
                    user_name=user_name,
                    field=next_field,
                )
                self._pending_profile_field = next_field
                dimension = ""
                topic_source = "collect_profile"
            else:
                next_topic = self._tg.get_next(user_id=profile.user_id)
                topic = next_topic["topic"]
                dimension = next_topic.get("dimension", "")
                topic_source = next_topic.get("source")
        else:
            next_topic = self._tg.get_next(user_id=profile.user_id)
            topic = next_topic["topic"]
            dimension = next_topic.get("dimension", "")
            topic_source = next_topic.get("source")
        self._current_topic = topic
        self._current_dimension = None

        # 构建唤醒上下文（新会话不返回，老会话返回）
        wakeup_context = ""
        if not is_new:
            wakeup_context = self._sm.build_wakeup_context(
                user_name,
                timestamp_iso,
            )

        debug_enabled = os.environ.get("MBTI_DEBUG") == "1"
        result: dict[str, object] = {
            "type": "next_topic",
            "topic": topic,
            "dimension": dimension,
            "topic_source": topic_source,
            "is_new": is_new,
            "wakeup_context": wakeup_context,
        }
        if debug_enabled:
            result["profile"] = profile.to_summary()
        return result

    def handle_response(
        self,
        user_name: str,
        timestamp_iso: str,
        user_response: str,
    ) -> dict:
        """
        处理用户对当前话题的回复。

        Args:
            user_name: 用户姓名
            timestamp_iso: 触发时间（用于生成 user_id）
            user_response: 用户回复文本

        Returns:
            {
                "type": "next_topic" | "report" | "archive",
                "topic": str,           # 下一话题（仅 next_topic）
                "dimension": str,       # 下一维度（仅 next_topic）
                "profile": dict,        # 当前画像摘要
                "quality": dict,        # 本轮评分详情
                "report": str,          # 分析报告（仅 report）
                "message": str,         # 结束提示（仅 archive）
                "should_archive": bool, # 是否应结束话题
                "archive_reason": str,  # 结束原因
            }
        """
        if self._sm is None or self._qc is None or self._tg is None:
            self.init()

        user_id = make_user_id(user_name, timestamp_iso)
        self._last_user_id = user_id
        debug_enabled = os.environ.get("MBTI_DEBUG") == "1"

        if self._awaiting_summary_feedback and self._last_summary is not None:
            handled = self._handle_summary_feedback(
                user_id=user_id,
                user_response=user_response,
            )
            if handled is not None:
                if debug_enabled:
                    profile_row = db.get_profile(user_id) or {}
                    if isinstance(profile_row, dict) and profile_row:
                        handled["profile"] = MBTIProfile.from_db_row(
                            profile_row
                        ).to_summary()
                return handled

        if self._pending_profile_field is not None:
            profile_row = db.get_profile(user_id)
            if not profile_row:
                return {
                    "type": "error",
                    "error": "missing_profile",
                    "message": "会话状态缺失，请重新发送 /MBTI <姓名> 触发。",
                }

            profile = MBTIProfile.from_db_row(profile_row)
            if self._is_report_request(user_response):
                self._pending_profile_field = None
                next_topic = self._tg.get_next(user_id=user_id)
                topic = next_topic["topic"]
                self._current_topic = topic
                self._current_dimension = None
                result: dict[str, object] = {
                    "type": "next_topic",
                    "topic": topic,
                    "dimension": "",
                    "topic_source": next_topic.get("source"),
                    "summary": None,
                    "report": None,
                    "message": None,
                    "should_archive": False,
                    "archive_reason": "继续",
                }
                if debug_enabled:
                    result["profile"] = profile.to_summary()
                return result

            handled = self._handle_profile_collection_turn(
                user_id=user_id,
                user_name=user_name,
                profile=profile,
                user_response=user_response,
            )
            if handled is not None:
                if debug_enabled:
                    handled["profile"] = MBTIProfile.from_db_row(
                        db.get_profile(user_id) or profile_row
                    ).to_summary()
                return handled

        # 1. 质量评估
        quality_result = self._qc.evaluate_round(
            user_id=user_id,
            topic=self._current_topic or "",
            user_response=user_response,
            dimension=None,
        )
        token_score = quality_result["token_score"]
        semantic_score = quality_result["semantic_score"]
        confidence = quality_result["confidence"]
        round_score = quality_result.get("round_score")
        should_archive = quality_result["should_archive"]
        archive_reason = quality_result["archive_reason"]

        # 2. 记录对话
        self._sm.record_round(
            user_id=user_id,
            topic=self._current_topic or "",
            user_response=user_response,
            dimension="",
            token_score=token_score,
            semantic_score=semantic_score,
            confidence=confidence,
        )

        signals = self._qc.analyze_dimension_signals(
            user_id=user_id,
            topic=self._current_topic or "",
            user_response=user_response,
        )
        evidence_strength = 0.6
        if isinstance(round_score, float):
            evidence_strength = 0.4 + 0.6 * max(0.0, min(1.0, round_score))
        profile = self._sm.update_from_dimension_signals(
            user_id=user_id,
            signals=signals,
            evidence_strength=evidence_strength,
            session_confidence=confidence,
        )

        # 4. 检查是否应输出报告
        round_count = len(db.get_conversation_history(user_id, limit=100))
        report = None
        summary = None
        topic = None
        dimension = None
        next_topic_source = None
        message = None
        want_report = self._is_report_request(user_response)

        if want_report:
            report = _render_report(profile, round_count)
            result_type = "report"
        elif should_archive:
            message = "这会儿我们聊得有点断，我先停一下。你想继续随时告诉我。"
            result_type = "archive"
        else:
            if self._should_give_light_summary(user_id, profile):
                summary = self._generate_light_summary(
                    user_id=user_id,
                    profile=profile,
                )
                self._last_summary = summary
                self._awaiting_summary_feedback = True
                result_type = "summary"
            else:
                next_topic = self._tg.get_next(user_id=user_id)
                topic = next_topic["topic"]
                dimension = next_topic.get("dimension", "")
                next_topic_source = next_topic.get("source")
                self._current_topic = topic
                self._current_dimension = None
                result_type = "next_topic"

        result: dict[str, object] = {
            "type": result_type,
            "topic": topic,
            "dimension": dimension,
            "topic_source": next_topic_source,
            "summary": summary,
            "report": report,
            "message": message,
            "should_archive": should_archive,
            "archive_reason": archive_reason,
        }
        if debug_enabled:
            result["profile"] = profile.to_summary()
            result["quality"] = {
                "token_score": token_score,
                "semantic_score": semantic_score,
                "semantic_source": quality_result.get("semantic_source"),
                "confidence": confidence,
                "repeat_contradiction_score": quality_result.get(
                    "repeat_contradiction_score"
                ),
                "round_score": quality_result.get("round_score"),
            }
            result["dimension_signals"] = signals
        return result

    def _is_report_request(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            return False
        return bool(
            re.search(r"^/(报告|report)\b", cleaned, flags=re.IGNORECASE)
            or re.search(r"(分析)?报告", cleaned)
            or re.search(r"(详细|完整版|完整).*?(分析|报告)", cleaned)
            or re.search(r"(给我|生成|输出).{0,6}(报告|分析)", cleaned)
        )

    def _handle_summary_feedback(
        self,
        *,
        user_id: str,
        user_response: str,
    ) -> dict[str, object] | None:
        text = user_response.strip()
        if not text:
            return None

        profile_row = db.get_profile(user_id)
        if not profile_row:
            return None
        profile = MBTIProfile.from_db_row(profile_row)

        if self._is_report_request(text):
            self._awaiting_summary_feedback = False
            self._last_summary = None
            report = _render_report(
                profile,
                len(db.get_conversation_history(user_id, limit=100)),
            )
            return {
                "type": "report",
                "topic": None,
                "dimension": None,
                "topic_source": None,
                "summary": None,
                "report": report,
                "message": None,
                "should_archive": False,
                "archive_reason": "用户请求报告",
            }

        is_affirm = bool(re.search(r"^(对|是|嗯|差不多|挺准|基本是)$", text))
        is_deny = bool(re.search(r"(不对|不太对|偏了|不是|相反|不准|误会)", text))

        if is_affirm:
            profile = self._sm.nudge_dimension_confidences(user_id=user_id, delta=0.05)
        elif is_deny:
            profile = self._sm.nudge_dimension_confidences(user_id=user_id, delta=-0.05)

        signals = self._qc.analyze_dimension_signals(
            user_id=user_id,
            topic=self._last_summary or "",
            user_response=text,
        )
        profile = self._sm.update_from_dimension_signals(
            user_id=user_id,
            signals=signals,
            evidence_strength=0.3,
            session_confidence=profile.confidence,
        )

        self._awaiting_summary_feedback = False
        self._last_summary = None

        if is_deny and len(text) < 15:
            topic = "那我想听你说说：你更希望我怎么理解你？能举个最近的例子吗？"
            self._current_topic = topic
            self._current_dimension = None
            return {
                "type": "next_topic",
                "topic": topic,
                "dimension": "",
                "topic_source": "summary_followup",
                "summary": None,
                "report": None,
                "message": None,
                "should_archive": False,
                "archive_reason": "继续",
            }

        next_topic = self._tg.get_next(user_id=user_id)
        topic = next_topic["topic"]
        self._current_topic = topic
        self._current_dimension = None
        return {
            "type": "next_topic",
            "topic": topic,
            "dimension": "",
            "topic_source": next_topic.get("source"),
            "summary": None,
            "report": None,
            "message": None,
            "should_archive": False,
            "archive_reason": "继续",
        }

    def _next_profile_field_to_collect(
        self,
        profile: MBTIProfile,
    ) -> str | None:
        if "gender" not in self._skipped_profile_fields and not profile.gender:
            return "gender"
        if (
            "birth_yyyymm" not in self._skipped_profile_fields
            and not profile.birth_yyyymm
        ):
            return "birth_yyyymm"
        if "occupation" not in self._skipped_profile_fields and not profile.occupation:
            return "occupation"
        return None

    def _build_profile_question(self, *, user_name: str, field: str) -> str:
        if field == "gender":
            return (
                f"{user_name}，我先确认一下，你更愿意我怎么称呼你？\n"
                "1) 男\n"
                "2) 女\n"
                "3) 其他\n"
                "4) 跳过\n"
                "直接回数字即可。"
            )
        if field == "birth_yyyymm":
            return (
                "你大概是哪年哪月出生的呀？我只需要到年月就行。\n"
                "请按 YYYYMM 输入（例：199803），或输入 0 跳过。"
            )
        if field == "occupation":
            return (
                "你现在主要做什么方向？选最接近的一项就行：\n"
                "1) 技术/产品\n"
                "2) 运营/市场\n"
                "3) 金融\n"
                "4) 教育\n"
                "5) 医疗\n"
                "6) 政企/事业单位\n"
                "7) 其他\n"
                "0) 跳过"
            )
        return "你可以简单说一句，也可以回“跳过”。"

    def _handle_profile_collection_turn(
        self,
        *,
        user_id: str,
        user_name: str,
        profile: MBTIProfile,
        user_response: str,
    ) -> dict[str, object] | None:
        field = self._pending_profile_field
        if field is None:
            return None

        if field == "birth_yyyymm" and self._pending_birth_confirmation is not None:
            handled = self._handle_birth_confirmation(
                user_id=user_id,
                user_name=user_name,
                profile=profile,
                user_response=user_response,
            )
            if handled is not None:
                return handled

        if self._is_skip_response(user_response):
            self._skipped_profile_fields.add(field)
            self._pending_profile_field = None
        else:
            if field == "gender":
                value = self._parse_gender(user_response)
                if value is None:
                    topic = (
                        "我只想确认一下称呼偏好：回 1(男) / 2(女) / 3(其他) / 4(跳过)。"
                    )
                    self._current_topic = topic
                    return {
                        "type": "next_topic",
                        "topic": topic,
                        "dimension": "",
                        "topic_source": "collect_profile_retry",
                        "summary": None,
                        "report": None,
                        "message": None,
                        "should_archive": False,
                        "archive_reason": "继续",
                    }
                db.update_profile(user_id, gender=value)
                self._pending_profile_field = None
            elif field == "birth_yyyymm":
                value = self._parse_birth_yyyymm(user_response)
                if value is None:
                    topic = "请按 YYYYMM 输入（例：199803），或输入 0 跳过。"
                    self._current_topic = topic
                    return {
                        "type": "next_topic",
                        "topic": topic,
                        "dimension": "",
                        "topic_source": "collect_profile_retry",
                        "summary": None,
                        "report": None,
                        "message": None,
                        "should_archive": False,
                        "archive_reason": "继续",
                    }
                if self._needs_birth_confirmation(value):
                    self._pending_birth_confirmation = value
                    topic = self._build_birth_confirmation_question(value)
                    self._current_topic = topic
                    return {
                        "type": "next_topic",
                        "topic": topic,
                        "dimension": "",
                        "topic_source": "collect_profile_confirm",
                        "summary": None,
                        "report": None,
                        "message": None,
                        "should_archive": False,
                        "archive_reason": "继续",
                    }
                db.update_profile(user_id, birth_yyyymm=value)
                self._pending_profile_field = None
            elif field == "occupation":
                value = self._parse_occupation(user_response)
                if value is None:
                    topic = "回 1~7 选项或输入 0 跳过就行。"
                    self._current_topic = topic
                    return {
                        "type": "next_topic",
                        "topic": topic,
                        "dimension": "",
                        "topic_source": "collect_profile_retry",
                        "summary": None,
                        "report": None,
                        "message": None,
                        "should_archive": False,
                        "archive_reason": "继续",
                    }
                if value == "其他":
                    self._skipped_profile_fields.add(field)
                    self._pending_profile_field = None
                else:
                    db.update_profile(user_id, occupation=value)
                    self._pending_profile_field = None
            else:
                self._skipped_profile_fields.add(field)
                self._pending_profile_field = None

        updated_row = db.get_profile(user_id) or profile.model_dump()
        updated_profile = (
            MBTIProfile.from_db_row(updated_row)
            if isinstance(updated_row, dict)
            else profile
        )
        next_field = self._next_profile_field_to_collect(updated_profile)
        if next_field is not None:
            topic = self._build_profile_question(
                user_name=user_name,
                field=next_field,
            )
            self._pending_profile_field = next_field
            self._current_topic = topic
            return {
                "type": "next_topic",
                "topic": topic,
                "dimension": "",
                "topic_source": "collect_profile",
                "summary": None,
                "report": None,
                "message": None,
                "should_archive": False,
                "archive_reason": "继续",
            }

        next_topic = self._tg.get_next(user_id=user_id)
        topic = next_topic["topic"]
        self._current_topic = topic
        self._current_dimension = None
        return {
            "type": "next_topic",
            "topic": topic,
            "dimension": "",
            "topic_source": next_topic.get("source"),
            "summary": None,
            "report": None,
            "message": None,
            "should_archive": False,
            "archive_reason": "继续",
        }

    def _is_skip_response(self, text: str) -> bool:
        cleaned = text.strip().lower()
        return cleaned in {
            "0",
            "4",
            "跳过",
            "不方便",
            "不方便说",
            "保密",
            "略过",
            "skip",
        }

    def _parse_gender(self, text: str) -> str | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        if cleaned in {"1", "男"} or re.search(r"(男|男性|男生)$", cleaned):
            return "男"
        if cleaned in {"2", "女"} or re.search(r"(女|女性|女生)$", cleaned):
            return "女"
        if cleaned in {"3", "其他"} or re.search(r"(其他|非二元|不确定)$", cleaned):
            return "其他"
        return None

    def _parse_birth_yyyymm(self, text: str) -> str | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        if cleaned == "0":
            return None
        match = re.search(
            r"(18\d{2}|19\d{2}|20\d{2})\D{0,3}([01]?\d)",
            cleaned,
        )
        if not match:
            match = re.search(r"^(18\d{2}|19\d{2}|20\d{2})([01]\d)$", cleaned)
        if not match:
            return None
        year = int(match.group(1))
        month = int(match.group(2))
        if month < 1 or month > 12:
            return None
        now = datetime.now(UTC)
        if year < 1850 or year > now.year:
            return None
        if year == now.year and month > now.month:
            return None
        return f"{year:04d}{month:02d}"

    def _needs_birth_confirmation(self, yyyymm: str) -> bool:
        try:
            year = int(yyyymm[:4])
            month = int(yyyymm[4:])
        except ValueError:
            return False

        if month < 1 or month > 12:
            return False

        now = datetime.now(UTC)
        age = now.year - year - (1 if (now.month, now.day) < (month, 1) else 0)
        return age > 90

    def _build_birth_confirmation_question(self, yyyymm: str) -> str:
        year = yyyymm[:4]
        month = yyyymm[4:]
        return (
            f"我看到你输入的是 {yyyymm}（{year}年{month}月）。"
            "这个年份会让年龄非常大，我怕是你手滑了。\n"
            "1) 确认就是这个\n"
            "2) 重新输入\n"
            "0) 跳过"
        )

    def _handle_birth_confirmation(
        self,
        *,
        user_id: str,
        user_name: str,
        profile: MBTIProfile,
        user_response: str,
    ) -> dict[str, object] | None:
        candidate = self._pending_birth_confirmation
        if candidate is None:
            return None

        cleaned = user_response.strip().lower()
        if cleaned in {"1", "确认", "是", "对"}:
            db.update_profile(user_id, birth_yyyymm=candidate)
            self._pending_birth_confirmation = None
            self._pending_profile_field = None
            updated_row = db.get_profile(user_id) or profile.model_dump()
            updated_profile = MBTIProfile.from_db_row(updated_row)
            next_field = self._next_profile_field_to_collect(updated_profile)
            if next_field is not None:
                topic = self._build_profile_question(
                    user_name=user_name,
                    field=next_field,
                )
                self._pending_profile_field = next_field
                self._current_topic = topic
                return {
                    "type": "next_topic",
                    "topic": topic,
                    "dimension": "",
                    "topic_source": "collect_profile",
                    "summary": None,
                    "report": None,
                    "message": None,
                    "should_archive": False,
                    "archive_reason": "继续",
                }

            next_topic = self._tg.get_next(user_id=user_id)
            topic = next_topic["topic"]
            self._current_topic = topic
            self._current_dimension = None
            return {
                "type": "next_topic",
                "topic": topic,
                "dimension": "",
                "topic_source": next_topic.get("source"),
                "summary": None,
                "report": None,
                "message": None,
                "should_archive": False,
                "archive_reason": "继续",
            }

        if cleaned in {"2", "重输", "重新", "重新输入"}:
            self._pending_birth_confirmation = None
            topic = "那你再输一次吧：按 YYYYMM 输入（例：199803），或输入 0 跳过。"
            self._current_topic = topic
            return {
                "type": "next_topic",
                "topic": topic,
                "dimension": "",
                "topic_source": "collect_profile_retry",
                "summary": None,
                "report": None,
                "message": None,
                "should_archive": False,
                "archive_reason": "继续",
            }

        if cleaned in {"0", "跳过"}:
            self._pending_birth_confirmation = None
            self._skipped_profile_fields.add("birth_yyyymm")
            self._pending_profile_field = None
            next_field = self._next_profile_field_to_collect(profile)
            if next_field is None:
                return None

            topic = self._build_profile_question(
                user_name=user_name,
                field=next_field,
            )
            self._pending_profile_field = next_field
            self._current_topic = topic
            return {
                "type": "next_topic",
                "topic": topic,
                "dimension": "",
                "topic_source": "collect_profile",
                "summary": None,
                "report": None,
                "message": None,
                "should_archive": False,
                "archive_reason": "继续",
            }

        topic = "回 1(确认) / 2(重新输入) / 0(跳过) 就行。"
        self._current_topic = topic
        return {
            "type": "next_topic",
            "topic": topic,
            "dimension": "",
            "topic_source": "collect_profile_confirm_retry",
            "summary": None,
            "report": None,
            "message": None,
            "should_archive": False,
            "archive_reason": "继续",
        }

    def _parse_occupation(self, text: str) -> str | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        if cleaned == "0":
            return None
        mapping = {
            "1": "技术/产品",
            "2": "运营/市场",
            "3": "金融",
            "4": "教育",
            "5": "医疗",
            "6": "政企/事业单位",
            "7": "其他",
        }
        if cleaned in mapping:
            return mapping[cleaned]
        if len(cleaned) > 60:
            cleaned = cleaned[:60]
        return cleaned

    def _should_give_light_summary(
        self,
        user_id: str,
        profile: MBTIProfile,
    ) -> bool:
        confidences = profile.dimension_confidences.model_dump()
        if not all(
            isinstance(confidences.get(k), float) and confidences[k] >= 0.7
            for k in ("EI", "SN", "TF", "JP")
        ):
            return False

        history = db.get_conversation_history(user_id, limit=3)
        recent_answers = [
            str(item.get("user_response", "")).strip() for item in history[-3:]
        ]
        low = sum(1 for t in recent_answers if self._is_low_engagement(t))
        return low >= 2

    def _is_low_engagement(self, text: str) -> bool:
        cleaned = text.strip()
        return len(cleaned) < 15 or bool(
            re.search(
                r"(不知道|随便|都行|还好|一般般|没啥|就这样|算了|不清楚)$",
                cleaned,
            )
        )

    def _generate_light_summary(
        self,
        *,
        user_id: str,
        profile: MBTIProfile,
    ) -> str:
        settings = load_openrouter_settings()
        history = db.get_conversation_history(user_id, limit=12)
        history_lines = "\n".join(
            (
                f"- 你问：{item.get('topic', '')}\n"
                f"  对方：{str(item.get('user_response', ''))[:120]}"
            )
            for item in history[-8:]
        )

        profile_lines = [
            f"性别：{profile.gender}" if profile.gender else None,
            f"出生年月：{profile.birth_yyyymm}" if profile.birth_yyyymm else None,
            f"职业：{profile.occupation}" if profile.occupation else None,
        ]
        profile_text = "\n".join([line for line in profile_lines if line]) or "（无）"

        if settings:
            prompt = (
                "你将输出一个非常轻量、口语化、试探性的总结，"
                "用来确认你对对方的理解。"
                "不要提 MBTI、人格类型、维度、测评、分析。\n\n"
                "要求：\n"
                "1) 2～4 句话，语气像朋友聊天\n"
                "2) 用“我有个感觉/我可能理解偏了”的口吻\n"
                "3) 最后一问邀请对方纠正或补充\n\n"
                f"对方画像（仅作语气参考）：\n{profile_text}\n\n"
                f"最近对话：\n{history_lines or '（无）'}\n"
            )
            content = call_chat_completion(
                settings=settings,
                messages=[
                    {"role": "system", "content": "你只输出总结文本。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
            )
            if content and content.strip():
                return content.strip()

        dims = profile.dimensions
        lines: list[str] = ["和你聊下来，我有个不一定准的感觉："]
        if dims.EI < 0.45:
            lines.append("你遇到事更倾向先自己消化，想清楚了再说。")
        elif dims.EI > 0.55:
            lines.append("你在互动里更容易被点燃，聊着聊着思路就更清晰。")
        if dims.JP < 0.45:
            lines.append("你做事更喜欢留一点弹性，边走边调整。")
        elif dims.JP > 0.55:
            lines.append("你更喜欢把事情理顺、定下来，这样心里更踏实。")
        return " ".join(lines) + " 你觉得我理解得对吗？哪里可能偏了？"


# ---------------------------------------------------------------------------
# 便捷函数（供 skill 系统调用）
# ---------------------------------------------------------------------------


def parse_trigger(text: str) -> str | None:
    """
    解析触发词，返回用户姓名。

    Args:
        text: 用户输入文本

    Returns:
        用户姓名，未匹配返回 None
    """
    match = TRIGGER_PATTERN.match(text.strip())
    if match:
        return match.group(1).strip()
    return None


def run(user_name: str, timestamp_iso: str | None = None) -> dict:
    """
    便捷运行函数。

    Args:
        user_name: 用户姓名
        timestamp_iso: 可选，默认为当前时间

    Returns:
        首次触发结果（next_topic）
    """
    if timestamp_iso is None:
        timestamp_iso = datetime.now(UTC).isoformat()

    skill = InsightSkill()
    return skill.handle_trigger(user_name, timestamp_iso)


def _run_repl(user_name: str) -> int:
    settings = load_openrouter_settings()
    if settings is None:
        print(
            "未检测到 OpenRouter 配置，将使用内置话题池与启发式评分。"
            "如需启用动态话题与 LLM 评分，请设置环境变量 "
            "OPENROUTER_API_KEY / OPENROUTER_MODEL，或在项目根目录创建 "
            ".openrouter.json（已在 .gitignore）。"
        )
    else:
        print(f"OpenRouter 已启用：model={settings.model}")

    timestamp_iso = datetime.now(UTC).isoformat()
    skill = InsightSkill()

    trigger_result = skill.handle_trigger(user_name, timestamp_iso)
    topic = trigger_result.get("topic")
    topic_source = trigger_result.get("topic_source")
    if isinstance(topic, str) and topic.strip():
        if topic_source:
            print(f"话题：{topic}  (source={topic_source})")
        else:
            print(f"话题：{topic}")
    else:
        print(json.dumps(trigger_result, ensure_ascii=False, indent=2))

    while True:
        try:
            user_response = input("你的回答> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_response:
            continue
        if user_response.lower() in {"exit", "quit", "/exit", "/quit"}:
            return 0

        result = skill.handle_response(
            user_name=user_name,
            timestamp_iso=timestamp_iso,
            user_response=user_response,
        )
        if result.get("type") == "report":
            report = result.get("report")
            if isinstance(report, str) and report.strip():
                print(report)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if result.get("type") == "archive":
            message = result.get("message")
            reason = result.get("archive_reason")
            if isinstance(message, str) and message.strip():
                if isinstance(reason, str) and reason.strip():
                    print(f"{message}  (reason={reason})")
                else:
                    print(message)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if result.get("type") == "summary":
            summary = result.get("summary")
            if isinstance(summary, str) and summary.strip():
                print(f"\n{summary.strip()}\n")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            continue

        if os.environ.get("MBTI_DEBUG") == "1":
            quality = result.get("quality") or {}
            profile = result.get("profile") or {}
            dims = profile.get("dimensions") or {}
            print(
                "\n本轮评分："
                f"token={quality.get('token_score')} "
                f"semantic={quality.get('semantic_score')} "
                f"(source={quality.get('semantic_source')}) "
                f"repeat={quality.get('repeat_contradiction_score')} "
                f"round={quality.get('round_score')} "
                f"confidence={quality.get('confidence')}"
            )
            print(
                "当前画像："
                f"type={profile.get('mbti_type')} "
                f"profile_conf={profile.get('confidence')} "
                f"EI={dims.get('EI')} SN={dims.get('SN')} "
                f"TF={dims.get('TF')} JP={dims.get('JP')}"
            )

        next_topic = result.get("topic")
        next_topic_source = result.get("topic_source")
        if isinstance(next_topic, str) and next_topic.strip():
            if next_topic_source:
                print(f"\n话题：{next_topic}  (source={next_topic_source})")
            else:
                print(f"\n话题：{next_topic}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None


def _extract_text(payload: dict[str, object]) -> str | None:
    for key in ("text", "input", "message", "content", "user_response"):
        value = _as_str(payload.get(key))
        if value:
            return value
    return None


def _extract_session_id(payload: dict[str, object]) -> str:
    for key in (
        "session_id",
        "session",
        "conversation_id",
        "thread_id",
        "run_id",
    ):
        value = _as_str(payload.get(key))
        if value:
            return value
    return datetime.now(UTC).isoformat()


def _extract_user_name(
    payload: dict[str, object],
    text: str | None,
) -> str | None:
    for key in ("user_name", "name", "user"):
        value = _as_str(payload.get(key))
        if value:
            return value
    if text:
        return parse_trigger(text)
    return None


def _is_trigger(text: str | None, payload: dict[str, object]) -> bool:
    event_type = _as_str(payload.get("type")) or _as_str(payload.get("event"))
    if text:
        return parse_trigger(text) is not None
    if event_type:
        return event_type.lower() in {"trigger", "start"}
    return False


def _extract_profile_fields(
    payload: dict[str, object],
) -> tuple[str | None, str | None, str | None]:
    gender = _as_str(payload.get("gender")) or _as_str(payload.get("sex"))
    birth_yyyymm = _as_str(payload.get("birth_yyyymm")) or _as_str(payload.get("birth"))
    occupation = _as_str(payload.get("occupation")) or _as_str(payload.get("job"))
    return gender, birth_yyyymm, occupation


def _run_skill_loop() -> int:
    import sys

    skills: dict[str, InsightSkill] = {}

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload_obj = json.loads(line)
            if not isinstance(payload_obj, dict):
                raise ValueError("payload must be a JSON object")
            payload: dict[str, object] = payload_obj

            text = _extract_text(payload)
            session_id = _extract_session_id(payload)
            user_name = _extract_user_name(payload, text)
            gender, birth_yyyymm, occupation = _extract_profile_fields(payload)

            if not user_name:
                raise ValueError("missing user_name")

            key = f"{user_name}:{session_id}"
            is_trigger = _is_trigger(text, payload)

            if is_trigger:
                skill = skills.get(key)
                if skill is None:
                    skill = InsightSkill()
                    skills[key] = skill
                result = skill.handle_trigger(
                    user_name=user_name,
                    timestamp_iso=session_id,
                    gender=gender,
                    birth_yyyymm=birth_yyyymm,
                    occupation=occupation,
                )
            else:
                skill = skills.get(key)
                if skill is None:
                    result = {
                        "type": "error",
                        "error": "missing_session_state",
                        "message": "会话状态缺失，请重新发送 /MBTI <姓名> 触发。",
                    }
                else:
                    user_response = text or ""
                    result = skill.handle_response(
                        user_name=user_name,
                        timestamp_iso=session_id,
                        user_response=user_response,
                    )

            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            error_result = {"type": "error", "error": str(exc)}
            print(json.dumps(error_result, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    # 简单测试
    raw_args = sys.argv[1:]
    if "--openrouter-model" in raw_args:
        idx = raw_args.index("--openrouter-model")
        if idx + 1 < len(raw_args):
            os.environ.setdefault("OPENROUTER_MODEL", raw_args[idx + 1])
            del raw_args[idx : idx + 2]

    if "--openrouter-config" in raw_args:
        idx = raw_args.index("--openrouter-config")
        if idx + 1 < len(raw_args):
            os.environ["OPENROUTER_CONFIG"] = raw_args[idx + 1]
            del raw_args[idx : idx + 2]

    if not raw_args and not sys.stdin.isatty():
        sys.exit(_run_skill_loop())

    if raw_args and raw_args[0] == "--repl":
        if len(raw_args) >= 2:
            sys.exit(_run_repl(raw_args[1]))
        sys.exit(_run_repl(input("请输入姓名> ").strip()))

    if raw_args:
        result = run(raw_args[0])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    print(
        "用法: python insight_skill.py <姓名>\n"
        "      python insight_skill.py --repl <姓名>\n"
        "可选参数: --openrouter-model <model> | --openrouter-config <path>"
    )
    sys.exit(2)

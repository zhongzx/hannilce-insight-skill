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
import os
import re
import statistics
from datetime import UTC, datetime
from difflib import SequenceMatcher

from mbti import db, models
from mbti.models import SlidingWindow
from openrouter_client import (
    call_chat_completion,
    extract_first_json_object,
    load_openrouter_settings,
)

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
        # result: {
        #   "token_score",
        #   "semantic_score",
        #   "semantic_source",
        #   "confidence",
        #   "should_archive",
        #   "archive_reason",
        # }
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
        dimension: str | None = None,
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

        repeat_contradiction_score = self._score_repeat_contradiction(
            user_id=user_id,
            dimension=dimension,
            user_response=user_response,
        )

        # 语义层评分
        if llm_semantic_score is not None:
            semantic_score = llm_semantic_score
            semantic_source = "provided"
        else:
            semantic_result = self._try_score_semantic_with_llm(
                topic=topic,
                user_response=user_response,
                user_id=user_id,
            )
            if semantic_result is None:
                semantic_score = self._score_semantic_fallback(
                    topic=topic,
                    user_response=user_response,
                )
                semantic_source = "fallback_heuristic"
            else:
                semantic_score, semantic_source = semantic_result

        round_score = (
            token_score * 0.3 + repeat_contradiction_score * 0.3 + semantic_score * 0.4
        )

        # 更新滑动窗口
        sw = self._load_or_create_window(user_id)
        sw.push(round_score)
        self._save_window(sw)

        confidence = sum(sw.recent_scores) / len(sw.recent_scores)
        should_archive, reason = self._should_finish_or_archive(
            user_id=user_id,
            current_dimension=dimension,
            session_confidence=confidence,
        )

        return {
            "token_score": round(token_score, 4),
            "semantic_score": round(semantic_score, 4),
            "semantic_source": semantic_source,
            "confidence": round(confidence, 4),
            "repeat_contradiction_score": round(repeat_contradiction_score, 4),
            "round_score": round(round_score, 4),
            "should_archive": should_archive,
            "archive_reason": reason,
        }

    def analyze_dimension_signals(
        self,
        *,
        user_id: str,
        topic: str,
        user_response: str,
    ) -> dict[str, float]:
        text = user_response.strip()
        if not text:
            return {"EI": 0.0, "SN": 0.0, "TF": 0.0, "JP": 0.0}
        if len(text) < 15:
            return {"EI": 0.0, "SN": 0.0, "TF": 0.0, "JP": 0.0}

        settings = load_openrouter_settings()
        if settings:
            result = self._analyze_dimension_signals_with_llm(
                user_id=user_id,
                topic=topic,
                user_response=user_response,
            )
            if result is not None:
                if len(text) < 30:
                    return {k: v * 0.5 for k, v in result.items()}
                return result

        return self._analyze_dimension_signals_heuristic(user_response)

    def _analyze_dimension_signals_with_llm(
        self,
        *,
        user_id: str,
        topic: str,
        user_response: str,
    ) -> dict[str, float] | None:
        settings = load_openrouter_settings()
        if not settings:
            return None

        history = db.get_conversation_history(user_id, limit=12)
        history_lines = "\n".join(
            (
                f"- Q: {item.get('topic', '')}\n"
                f"  A: {str(item.get('user_response', ''))[:120]}"
            )
            for item in history[-6:]
        )

        prompt = (
            "你在后台做性格倾向信号提取，用于更新四个维度的证据强度。"
            "不要输出任何解释，只输出 JSON。\n\n"
            "维度与方向定义（数值范围 -1.0 ~ +1.0，0 表示无信号）：\n"
            "- EI：+ 越偏外向(E)，- 越偏内向(I)\n"
            "- SN：+ 越偏实感(S)，- 越偏直觉(N)\n"
            "- TF：+ 越偏思考(T)，- 越偏情感(F)\n"
            "- JP：+ 越偏判断(J)，- 越偏知觉(P)\n\n"
            "规则：\n"
            "- 只从文本中可见的表达提取，不要凭空脑补。\n"
            "- 如果证据很弱，请接近 0。\n"
            '- 输出格式固定为：{"EI": 0.0, "SN": 0.0, "TF": 0.0, "JP": 0.0}\n\n'
            f"最近对话：\n{history_lines or '（无）'}\n\n"
            f"本轮问题：{topic}\n\n"
            f"用户回答：{user_response}\n"
        )

        content = self._call_llm_with_retry(
            settings=settings,
            messages=[
                {"role": "system", "content": "你只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_attempts=2,
        )
        if not content:
            return None

        obj = extract_first_json_object(content)
        if not isinstance(obj, dict):
            return None

        parsed: dict[str, float] = {}
        for key in ("EI", "SN", "TF", "JP"):
            raw = obj.get(key)
            if isinstance(raw, (int, float)):
                value = float(raw)
            elif isinstance(raw, str):
                try:
                    value = float(raw.strip())
                except ValueError:
                    value = 0.0
            else:
                value = 0.0
            parsed[key] = max(-1.0, min(1.0, value))

        return parsed

    def _analyze_dimension_signals_heuristic(
        self,
        user_response: str,
    ) -> dict[str, float]:
        text = user_response.strip()
        if not text:
            return {"EI": 0.0, "SN": 0.0, "TF": 0.0, "JP": 0.0}

        def score(pos: set[str], neg: set[str]) -> float:
            pos_hits = sum(1 for w in pos if w in text)
            neg_hits = sum(1 for w in neg if w in text)
            raw = pos_hits - neg_hits
            if raw == 0:
                return 0.0
            return max(-1.0, min(1.0, raw / 4.0))

        ei = score(
            {"社交", "聚会", "人群", "聊天", "交流", "朋友", "团队", "一起"},
            {"独处", "一个人", "安静", "宅", "自己想", "不爱社交"},
        )
        sn = score(
            {"细节", "具体", "一步步", "落地", "执行", "事实", "经验"},
            {"抽象", "可能性", "趋势", "直觉", "灵感", "愿景", "感觉"},
        )
        tf = score(
            {"逻辑", "分析", "理性", "客观", "效率", "原则", "合理"},
            {"感受", "情绪", "在意", "共情", "关系", "温柔", "舒服"},
        )
        jp = score(
            {"计划", "安排", "清单", "提前", "有序", "确定", "按部就班"},
            {"随性", "随机", "临时", "灵活", "看情况", "再说", "即兴"},
        )

        return {"EI": ei, "SN": sn, "TF": tf, "JP": jp}

    def _score_semantic_fallback(
        self,
        *,
        topic: str,
        user_response: str,
    ) -> float:
        base = _DEFAULT_SEMANTIC_SCORE
        topic_clean = self._normalize_text(topic)
        response_clean = self._normalize_text(user_response)
        if len(response_clean) < 10:
            return 0.0

        overlap = self._jaccard_similarity(
            self._char_ngrams(topic_clean, n=2),
            self._char_ngrams(response_clean, n=2),
        )

        seq_ratio = SequenceMatcher(
            None,
            topic_clean[:240],
            response_clean[:600],
        ).ratio()
        combined = overlap * 0.7 + seq_ratio * 0.3

        if combined <= 0.015:
            return min(0.2, base)
        if combined <= 0.03:
            return min(0.3, base)
        if combined <= 0.06:
            return min(0.45, base)
        if combined <= 0.12:
            return base

        boosted = base + (combined - 0.12) * 1.2
        return max(0.0, min(1.0, boosted))

    def _normalize_text(self, text: str) -> str:
        keep = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
        return "".join(keep).lower()

    def _char_ngrams(self, text: str, *, n: int) -> set[str]:
        if n <= 0:
            return set()
        limited = text[:1200]
        if len(limited) < n:
            return {limited} if limited else set()
        return {limited[i : i + n] for i in range(len(limited) - n + 1)}

    def _jaccard_similarity(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = a & b
        union = a | b
        return len(inter) / len(union)

    def _try_score_semantic_with_llm(
        self,
        *,
        topic: str,
        user_response: str,
        user_id: str,
    ) -> tuple[float, str] | None:
        settings = load_openrouter_settings()
        if not settings:
            return None

        if len(user_response.strip()) < 10:
            return 0.0, "llm_short"

        history = db.get_conversation_history(user_id, limit=10)
        history_lines = "\n".join(
            (
                f"- Q: {item.get('topic', '')}\n"
                f"  A: {str(item.get('user_response', ''))[:80]}"
            )
            for item in history[-5:]
        )
        prompt = self.build_semantic_prompt(
            topic=topic,
            user_response=user_response,
            history=history_lines,
        )

        per_call_scores: list[float] = []
        sample_count = self._semantic_sample_count(settings.model)
        for _ in range(sample_count):
            content = self._call_llm_with_retry(
                settings=settings,
                messages=[
                    {
                        "role": "system",
                        "content": "你是 MBTI 对话质量评估员，只输出 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_attempts=2,
            )
            if not content:
                continue

            data = extract_first_json_object(content)
            if not isinstance(data, dict):
                continue

            semantic = self._clip_0_10(data.get("semantic"))
            consistency = self._clip_0_10(data.get("consistency"))
            authenticity = self._clip_0_10(data.get("authenticity"))
            if semantic is None or consistency is None or authenticity is None:
                continue

            avg_10 = (semantic + consistency + authenticity) / 3.0
            per_call_scores.append(avg_10)

        if not per_call_scores:
            return None

        robust_10 = self._robust_average_10(per_call_scores)
        score_0_1 = self._clip_0_1(robust_10 / 10.0)
        if len(per_call_scores) <= 1:
            return score_0_1, "llm_single"
        return score_0_1, f"llm_median{len(per_call_scores)}"

    def _semantic_sample_count(self, model: str) -> int:
        raw = os.environ.get("OPENROUTER_SEMANTIC_SAMPLES")
        if raw:
            try:
                value = int(raw)
            except ValueError:
                value = 0
            if value <= 0:
                return 1
            return max(1, min(3, value))

        if "free" in model:
            return 1
        return 3

    def _call_llm_with_retry(
        self,
        *,
        settings,
        messages: list[dict[str, str]],
        temperature: float,
        max_attempts: int,
    ) -> str | None:
        for attempt in range(max_attempts):
            content = call_chat_completion(
                settings=settings,
                messages=messages,
                temperature=temperature,
            )
            if content:
                return content
            if attempt < max_attempts - 1:
                continue
        return None

    def _robust_average_10(self, values: list[float]) -> float:
        if len(values) == 1:
            return values[0]
        median = statistics.median(values)
        filtered = [v for v in values if abs(v - median) <= 3.0]
        if not filtered:
            return float(median)
        return sum(filtered) / len(filtered)

    def _clip_0_10(self, value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        v = float(value)
        if v < 0.0:
            return 0.0
        if v > 10.0:
            return 10.0
        return v

    def _clip_0_1(self, value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    def _score_repeat_contradiction(
        self,
        *,
        user_id: str,
        dimension: str | None,
        user_response: str,
    ) -> float:
        history = db.get_conversation_history(user_id, limit=10)
        recent_answers = [
            str(item.get("user_response", "")).strip()
            for item in history[-3:]
            if str(item.get("user_response", "")).strip()
        ]
        if not recent_answers:
            return 1.0

        current = user_response.strip()
        if not current:
            return 0.0

        max_similarity = max(
            SequenceMatcher(None, current, prev).ratio() for prev in recent_answers
        )
        repetition_score = 1.0 - max_similarity
        repetition_score = max(0.0, min(1.0, repetition_score))

        contradiction_score = 1.0
        if dimension:
            contradiction_score = self._score_dimension_contradiction(
                dimension=dimension,
                history=history,
                current=current,
            )

        return repetition_score * 0.5 + contradiction_score * 0.5

    def _score_dimension_contradiction(
        self,
        *,
        dimension: str,
        history: list[dict],
        current: str,
    ) -> float:
        pairs_by_dim: dict[str, list[tuple[str, str]]] = {
            "EI": [("社交", "独处"), ("热闹", "安静")],
            "SN": [("细节", "整体"), ("具体", "抽象")],
            "TF": [("理性", "感受"), ("逻辑", "情绪")],
            "JP": [("计划", "随性"), ("安排", "临时")],
        }
        pairs = pairs_by_dim.get(dimension, [])
        if not pairs:
            return 1.0

        def polarity(text: str) -> int:
            neg = {"不", "没", "不是", "并非", "不太", "不怎么", "很少"}
            score = 0
            for a, b in pairs:
                if a in text and b in text:
                    continue
                if a in text:
                    score += -1 if any(n in text for n in neg) else 1
                if b in text:
                    score += 1 if any(n in text for n in neg) else -1
            return score

        current_p = polarity(current)
        if current_p == 0:
            return 1.0

        past = [
            str(item.get("user_response", ""))
            for item in history
            if item.get("dimension") == dimension
        ]
        if not past:
            return 1.0

        past_p = sum(polarity(t) for t in past[-5:])
        if past_p == 0:
            return 1.0

        if (current_p > 0 and past_p < 0) or (current_p < 0 and past_p > 0):
            return 0.3
        return 1.0

    def _should_finish_or_archive(
        self,
        *,
        user_id: str,
        current_dimension: str | None,
        session_confidence: float,
    ) -> tuple[bool, str]:
        _ = current_dimension
        _ = session_confidence

        sw = self._load_or_create_window(user_id)
        should_archive, reason = sw.should_archive()
        if should_archive:
            return True, reason

        return False, "继续"

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
        return token_score * 0.3 + semantic_score * 0.5 + 0.2

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
            now = datetime.now(UTC).isoformat()
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
            INSERT OR REPLACE INTO sliding_window
                (user_id, recent_scores, window_size, last_updated)
            VALUES (?, ?, ?, ?)
            """,
            (
                sw.user_id,
                json.dumps(sw.recent_scores),
                sw.window_size,
                sw.last_updated,
            ),
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
        history: str,
    ) -> str:
        """
        构建 LLM 语义评分 prompt。

        调用方将此 prompt 发送给 LLM，LLM 返回 JSON：
            {"semantic": 0-10, "consistency": 0-10, "authenticity": 0-10}

        Args:
            topic: 本轮话题
            user_response: 用户回复原文

        Returns:
            完整的 prompt 字符串
        """
        return (
            "你是 MBTI 对话质量评估员。请对用户回答进行三项打分（0-10分）：\n"
            "1. semantic（语义丰富度：是否表达了观点、理由、感受）\n"
            "2. consistency（一致性：是否与历史回答矛盾？若矛盾则低分）\n"
            "3. authenticity（真诚度：是否像真实回答，而非敷衍或套话）\n\n"
            "规则：\n"
            "- 如果用户回答少于 10 个字，semantic 自动为 0。\n"
            "- 如果用户回答明显重复上一轮内容，consistency 分数应 ≤ 3。\n"
            "- 不要因为回答符合某种人格就刻意打高分或低分。\n"
            '- 输出格式：{"semantic": 7, "consistency": 8, "authenticity": 6}\n'
            "只输出 JSON。\n\n"
            f"本轮话题：{topic}\n\n"
            f"历史摘要：\n{history}\n\n"
            f"用户回复：{user_response}\n"
        )

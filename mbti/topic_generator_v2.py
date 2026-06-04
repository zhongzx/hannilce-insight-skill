from __future__ import annotations

import json
import random
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from mbti import db
from openrouter_client import (
    OpenRouterSettings,
    call_chat_completion,
    load_openrouter_settings,
)


class TopicGeneratorV2:
    def __init__(self, seed_path: str | Path | None = None) -> None:
        if seed_path is None:
            base = Path(__file__).parent.parent
            seed_path = base / "seed_data" / "scraped_topics.json"
        self.seed_path = Path(seed_path)
        self._seed_categories = self._load_seed_categories()

    def get_next(
        self,
        *,
        user_id: str,
        exclude_topics: list[str] | None = None,
    ) -> dict[str, str]:
        exclude_topics = exclude_topics or []
        history = db.get_conversation_history(user_id, limit=50)
        asked_topics = {
            str(log.get("topic", "")).strip()
            for log in history
            if str(log.get("topic", "")).strip()
        } | {t.strip() for t in exclude_topics if t.strip()}
        asked_topics_norm = {self._normalize_topic(t) for t in asked_topics}

        conversation_mode = self._infer_conversation_mode(history)

        profile = db.get_profile(user_id) or {}
        gender = (
            str(profile.get("gender")).strip()
            if isinstance(profile.get("gender"), str)
            else None
        )
        birth_yyyymm = (
            str(profile.get("birth_yyyymm")).strip()
            if isinstance(profile.get("birth_yyyymm"), str)
            else None
        )
        occupation = (
            str(profile.get("occupation")).strip()
            if isinstance(profile.get("occupation"), str)
            else None
        )

        settings = load_openrouter_settings()
        topic = None
        source = "fallback"
        if settings:
            topic = self._generate_topic_with_llm(
                settings=settings,
                history=history,
                asked_topics=asked_topics,
                asked_topics_norm=asked_topics_norm,
                conversation_mode=conversation_mode,
                gender=gender,
                birth_yyyymm=birth_yyyymm,
                occupation=occupation,
            )
            if (
                topic is not None
                and topic not in asked_topics
                and self._normalize_topic(topic) not in asked_topics_norm
            ):
                source = "openrouter"

        if (
            topic is None
            or topic in asked_topics
            or self._normalize_topic(topic) in asked_topics_norm
        ):
            topic = self._fallback_topic(
                conversation_mode=conversation_mode,
                asked_topics=asked_topics,
            )

        return {
            "topic": topic,
            "dimension": "",
            "source": source,
        }

    def _infer_conversation_mode(self, history: list[dict]) -> str:
        if not history:
            return "open"

        last = history[-1]
        last_answer = str(last.get("user_response", "")).strip()
        if len(last_answer) < 15:
            return "pivot"
        return "deepen"

    def _load_seed_categories(self) -> list[str]:
        if not self.seed_path.exists():
            return []

        try:
            raw = self.seed_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return []

        categories: list[str] = []

        if isinstance(data, dict) and isinstance(data.get("sources"), dict):
            for source in data["sources"].values():
                if not isinstance(source, dict):
                    continue
                topics = source.get("topics")
                if not isinstance(topics, list):
                    continue
                for item in topics:
                    if not isinstance(item, dict):
                        continue
                    category = item.get("category")
                    if isinstance(category, str) and category.strip():
                        categories.append(category.strip())
            return categories

        items = data if isinstance(data, list) else data.get("topics", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                category = item.get("category")
                if isinstance(category, str) and category.strip():
                    categories.append(category.strip())
        return categories

    def _select_seed_snippets(
        self,
        *,
        gender: str | None,
        birth_yyyymm: str | None,
        occupation: str | None,
        k: int,
    ) -> list[str]:
        if not self._seed_categories:
            return []

        preferred = self._preferred_category_keywords(
            gender=gender,
            birth_yyyymm=birth_yyyymm,
            occupation=occupation,
        )
        if preferred:
            filtered = [
                c
                for c in self._seed_categories
                if any(keyword in c for keyword in preferred)
            ]
        else:
            filtered = []

        pool = filtered or self._seed_categories
        cats = list(dict.fromkeys(pool))
        chosen = cats if len(cats) <= k else random.sample(cats, k)
        return [f"{c} 相关热点" for c in chosen]

    def _preferred_category_keywords(
        self,
        *,
        gender: str | None,
        birth_yyyymm: str | None,
        occupation: str | None,
    ) -> set[str]:
        keywords: set[str] = set()
        age = self._age_from_yyyymm(birth_yyyymm) if birth_yyyymm else None

        if occupation:
            lowered = occupation.lower()
            tech_words = ("ai", "ml", "llm", "dev", "engineer")
            if any(
                w in occupation for w in ("程序", "开发", "工程", "算法", "数据")
            ) or any(w in lowered for w in tech_words):
                keywords |= {"科技", "AI", "数码", "3C", "前沿"}
            if any(w in occupation for w in ("金融", "投资", "证券", "会计", "财务")):
                keywords |= {"股市", "商业", "财经"}
            if any(w in occupation for w in ("医生", "护士", "医疗", "健康", "心理")):
                keywords |= {"健康"}
            if any(w in occupation for w in ("媒体", "内容", "编辑", "运营", "市场")):
                keywords |= {"观点", "商业", "娱乐"}

        if age is not None and age < 25:
            keywords |= {"娱乐", "游戏"}

        if gender:
            _ = gender

        return keywords

    def _age_from_yyyymm(self, birth_yyyymm: str) -> int | None:
        text = birth_yyyymm.strip()
        if len(text) != 6 or not text.isdigit():
            return None
        year = int(text[:4])
        month = int(text[4:])
        if month < 1 or month > 12:
            return None
        now = datetime.now(UTC)
        age = now.year - year - (1 if (now.month, now.day) < (month, 1) else 0)
        return age if age >= 0 else None

    def _generate_topic_with_llm(
        self,
        *,
        settings: OpenRouterSettings,
        history: list[dict],
        asked_topics: set[str],
        asked_topics_norm: set[str],
        conversation_mode: str,
        gender: str | None,
        birth_yyyymm: str | None,
        occupation: str | None,
    ) -> str | None:
        seed_snippets = self._select_seed_snippets(
            gender=gender,
            birth_yyyymm=birth_yyyymm,
            occupation=None,
            k=2,
        )
        seed_block = "\n".join(f"- {s}" for s in seed_snippets) or "（无）"
        history_lines = "\n".join(
            (
                f"- Q: {item.get('topic', '')}\n"
                f"  A: {str(item.get('user_response', ''))[:80]}"
            )
            for item in history[-5:]
        )
        asked_preview = "\n".join(list(asked_topics)[:12]) or "（无）"
        profile_lines = [
            f"性别：{gender}" if gender else None,
            f"出生年月：{birth_yyyymm}" if birth_yyyymm else None,
        ]
        profile_text_lines = [line for line in profile_lines if line]
        profile_text = "\n".join(profile_text_lines) or "（无）"

        strategy = {
            "open": "用一个非常开放的问题开始，让对方愿意多说一点。",
            "deepen": "优先追问刚才那点的细节、动机或当时的感受，让话题自然深入。",
            "pivot": "不要追问太深，换一个更轻松、开放的新方向，避免让对方有压力。",
        }.get(conversation_mode, "根据对话自然延续。")

        prompt = (
            "你是一位敏锐、真诚、很好聊的朋友。你的目标是让对话自然流动，"
            "让对方愿意继续说。\n\n"
            "硬性要求：不要提 MBTI、人格类型、维度、测评、分析，也不要出二选一/量表问题。\n\n"
            f"用户画像（仅用于提问语气与选题）：\n{profile_text}\n\n"
            "给你一些“种子素材”作为灵感锚点（可能来自新闻摘要）。你只能把它当作主题方向，"
            "绝对不要提到任何真实具体的事件、人物、公司、地点或时间。\n\n"
            f"对话策略：{strategy}\n\n"
            f"种子素材（仅作抽象灵感）：\n{seed_block}\n\n"
            f"最近对话（避免重复）：\n{history_lines or '（无）'}\n\n"
            f"已问过话题（截断）：\n{asked_preview}\n\n"
            "要求：\n"
            "1) 只输出一行中文口语对话文本（<=30字），不要输出 JSON，不要解释\n"
            "2) 允许：一句共情/复述（可选）+ 一个简短问题（可选），整行最多一个问号\n"
            "3) 禁止采访腔：不要用“能否/请你/你能不能/讲述一次/如何看待/你是否”等\n"
            "4) 除非对方主动提到，否则避免行业术语或职业细节\n"
            "5) 避免与已问过话题重复\n"
            "6) 不要夹英文或拼音\n"
            "7) 不要输出空泛的“你好/请继续/好的”，必须给出一个可回答的具体点\n"
        )
        content = self._call_llm_topic_with_retry(
            settings=settings,
            prompt=prompt,
            max_attempts=3,
        )
        if content:
            candidate = self._sanitize_candidate(content)
            if (
                candidate
                and self._is_actionable_prompt(candidate)
                and not self._is_similar_to_any(
                    candidate,
                    asked_topics=asked_topics,
                    asked_topics_norm=asked_topics_norm,
                )
            ):
                return candidate

        second = self._call_llm_topic_with_retry(
            settings=settings,
            prompt=prompt,
            max_attempts=2,
        )
        if not second:
            return None
        candidate2 = self._sanitize_candidate(second)
        if not candidate2 or not self._is_actionable_prompt(candidate2):
            return None
        if self._is_similar_to_any(
            candidate2,
            asked_topics=asked_topics,
            asked_topics_norm=asked_topics_norm,
        ):
            return None
        return candidate2

    def _normalize_topic(self, text: str) -> str:
        cleaned = text.strip().replace("您", "你")
        parts = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", cleaned)
        return "".join(parts).lower()

    def _sanitize_candidate(self, text: str) -> str:
        candidate = text.strip().splitlines()[0].strip()
        return candidate.strip(" \"'“”‘’")

    def _is_actionable_prompt(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if len(t) > 36:
            return False
        if "MBTI" in t or "维度" in t:
            return False
        if "?" in t or "？" in t:
            return True
        if t.endswith(("吗", "呢", "呀", "么", "吧")):
            return True
        return any(key in t for key in ("说说", "聊聊", "讲讲", "展开", "具体", "细说"))

    def _is_similar_to_any(
        self,
        candidate: str,
        *,
        asked_topics: set[str],
        asked_topics_norm: set[str],
    ) -> bool:
        candidate_norm = self._normalize_topic(candidate)
        if not candidate_norm:
            return True
        if candidate_norm in asked_topics_norm:
            return True

        from difflib import SequenceMatcher

        for prev in asked_topics:
            prev_norm = self._normalize_topic(prev)
            if not prev_norm:
                continue
            if SequenceMatcher(None, candidate_norm, prev_norm).ratio() >= 0.9:
                return True
        return False

    def _call_llm_topic_with_retry(
        self,
        *,
        settings: OpenRouterSettings,
        prompt: str,
        max_attempts: int,
    ) -> str | None:
        messages = [
            {
                "role": "system",
                "content": "你只输出一行中文口语对话文本（不超过 30 字）。",
            },
            {"role": "user", "content": prompt},
        ]
        for attempt in range(max_attempts):
            content = call_chat_completion(
                settings=settings,
                messages=messages,
                temperature=0.4,
            )
            if content:
                return content
            if attempt < max_attempts - 1:
                time.sleep(0.2 * (attempt + 1))
        return None

    def _fallback_topic(
        self,
        *,
        conversation_mode: str,
        asked_topics: set[str],
    ) -> str:
        fallback: dict[str, list[str]] = {
            "open": [
                "最近有没有什么让你特别投入或者放不下的事情？",
                "这段时间你最在意的一件事是什么？",
                "最近你过得怎么样？有没有一件事让你印象很深？",
                "最近你感觉自己被什么东西“推着走”吗？",
            ],
            "deepen": [
                "你刚才提到的那件事，最让你在意的点是什么？",
                "当时你心里第一反应是什么？后来又怎么想的？",
                "你愿意具体讲讲那一刻发生了什么吗？",
                "如果把它拆开看，你觉得最难的部分是哪一段？",
            ],
            "pivot": [
                "那我们换个轻松点的：最近有什么让你觉得“挺不错”的小事吗？",
                "最近你有在期待什么吗？哪怕很小也行。",
                "这段时间你更想把精力放在哪儿？为什么？",
                "如果给你一个完全自由的周末，你最想怎么过？",
            ],
        }
        candidates = fallback.get(conversation_mode, fallback["open"])
        unused = [q for q in candidates if q not in asked_topics]
        pool = unused or candidates
        return random.choice(pool)

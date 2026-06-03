from __future__ import annotations

import json
import random
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
            seed_path = (
                Path(__file__).parent.parent / "seed_data" / "scraped_topics.json"
            )
        self.seed_path = Path(seed_path)
        self._seed_categories = self._load_seed_categories()

    def get_next(
        self,
        *,
        user_id: str,
        dimension: str | None = None,
        avoid_dimensions: list[str] | None = None,
        exclude_topics: list[str] | None = None,
    ) -> dict[str, str]:
        exclude_topics = exclude_topics or []
        avoid_dimensions = avoid_dimensions or []
        history = db.get_conversation_history(user_id, limit=50)
        asked_topics = {log["topic"] for log in history} | set(exclude_topics)

        target_dimension = dimension or self._choose_next_dimension(
            history,
            avoid_dimensions=set(avoid_dimensions),
        )

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
        if settings:
            topic = self._generate_topic_with_llm(
                settings=settings,
                history=history,
                asked_topics=asked_topics,
                target_dimension=target_dimension,
                gender=gender,
                birth_yyyymm=birth_yyyymm,
                occupation=occupation,
            )

        if topic is None or topic in asked_topics:
            topic = self._fallback_topic(target_dimension)

        return {
            "topic": topic,
            "dimension": target_dimension,
            "source": "llm_v2",
        }

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

    def _choose_next_dimension(
        self,
        history: list[dict],
        *,
        avoid_dimensions: set[str],
    ) -> str:
        dims = ["EI", "SN", "TF", "JP"]
        counts = dict.fromkeys(dims, 0)
        for item in history:
            dim = item.get("dimension")
            if isinstance(dim, str) and dim in counts:
                counts[dim] += 1

        eligible = [d for d in dims if d not in avoid_dimensions]
        pool = eligible or dims
        min_count = min(counts[d] for d in pool)
        candidates = [d for d in pool if counts[d] == min_count]
        return random.choice(candidates)

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
        categories = list(dict.fromkeys(pool))
        chosen = categories if len(categories) <= k else random.sample(categories, k=k)
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
            if any(
                w in occupation for w in ("程序", "开发", "工程", "算法", "数据")
            ) or any(w in lowered for w in ("ai", "ml", "llm", "dev", "engineer")):
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
        target_dimension: str,
        gender: str | None,
        birth_yyyymm: str | None,
        occupation: str | None,
    ) -> str | None:
        seed_snippets = self._select_seed_snippets(
            gender=gender,
            birth_yyyymm=birth_yyyymm,
            occupation=occupation,
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
            f"职业：{occupation}" if occupation else None,
        ]
        profile_text = "\n".join([line for line in profile_lines if line]) or "（无）"

        prompt = (
            "你是一名擅长深度访谈的提问者。你的目标是通过自然聊天了解一个人的偏好，"
            "但不要直接提 MBTI，不要出选择题。\n\n"
            f"用户画像（仅用于提问语气与选题）：\n{profile_text}\n\n"
            "给你一些“种子素材”作为灵感锚点（可能来自新闻摘要）。你只能把它当作主题方向，"
            "绝对不要提到任何真实具体的事件、人物、公司、地点或时间。\n\n"
            f"优先探索维度：{target_dimension}\n\n"
            f"种子素材（仅作抽象灵感）：\n{seed_block}\n\n"
            f"最近对话（避免重复）：\n{history_lines or '（无）'}\n\n"
            f"已问过话题（截断）：\n{asked_preview}\n\n"
            "要求：\n"
            "1) 只输出一行中文问题，不要输出 JSON，不要解释\n"
            "2) 问题必须开放式，鼓励用户讲具体经历/例子/感受\n"
            "3) 避免与已问过话题重复\n"
        )
        content = call_chat_completion(
            settings=settings,
            messages=[
                {"role": "system", "content": "你只输出一行问题文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        if not content:
            return None

        first_line = content.strip().splitlines()[0].strip()
        return first_line or None

    def _fallback_topic(self, target_dimension: str) -> str:
        fallback_map = {
            "EI": "你最近一次和别人相处让你感到“充电”的时刻是什么？当时发生了什么？",
            "SN": "你做决定时更看重具体细节还是整体方向？能用一个最近的例子说明吗？",
            "TF": "最近有一件让你纠结的事吗？你当时更在意道理还是更在意感受？",
            "JP": "你更喜欢把事情提前规划好，还是边走边调整？最近一次体现这一点的经历是什么？",
        }
        return fallback_map.get(target_dimension, fallback_map["EI"])

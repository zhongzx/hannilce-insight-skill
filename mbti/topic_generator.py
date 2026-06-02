"""
TopicGenerator: 动态话题生成 + 内置话题池兜底

职责：
    1. LLM 动态生成话题（基于用户画像上下文）
    2. 内置话题池兜底（seed_data/scraped_topics.json）
    3. 过期话题自动剔除
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from mbti import db

# ---------------------------------------------------------------------------
# 内置话题池（静态兜底）
# ---------------------------------------------------------------------------
_BUILTIN_TOPICS: list[dict] = [
    # EI 维度 - 外向/内向
    {
        "topic": "你最近参加社交活动的频率如何？什么样的场合最让你感到精力充沛？",
        "dimension": "EI",
    },
    {
        "topic": "独处对你来说是充电还是消耗？你享受什么样的独处时光？",
        "dimension": "EI",
    },
    {
        "topic": "你在陌生人多的场合通常是什么状态？是怎么逐步适应新环境的？",
        "dimension": "EI",
    },
    {
        "topic": "描述一个你最近主动发起的聚会或活动，是什么动机让你组织的？",
        "dimension": "EI",
    },
    {"topic": "当你一个人待太久时，身体或心理上会有什么反应？", "dimension": "EI"},
    {"topic": "朋友多对你来说意味着什么？数量和质量你怎么权衡？", "dimension": "EI"},
    {"topic": "在团队讨论中，你是更倾向于先听还是先说？", "dimension": "EI"},
    {"topic": "遇到问题时，你更习惯一个人想还是找人聊聊？", "dimension": "EI"},
    {
        "topic": "你会在社交媒体上分享日常生活吗？频率和内容有什么讲究？",
        "dimension": "EI",
    },
    {
        "topic": "回忆一次你突然被推到人群中央的经历，当时你的感受和反应是什么？",
        "dimension": "EI",
    },
    # SN 维度 - 实感/直觉
    {
        "topic": "你更关注事情落地的细节还是长远可能性？能举个例子吗？",
        "dimension": "SN",
    },
    {"topic": "你有没有过某种'直觉'后来被证明很准的经历？", "dimension": "SN"},
    {
        "topic": "你更喜欢处理具体的、看得见的问题，还是抽象的、可能性多的问题？",
        "dimension": "SN",
    },
    {"topic": "看地图和读说明书这类需要细节感的事情，你擅长吗？", "dimension": "SN"},
    {"topic": "你有没有过完全凭感觉做了一个重要决定？结果怎么样？", "dimension": "SN"},
    {
        "topic": "你更相信经验总结出来的规律，还是愿意打破框架找新的解法？",
        "dimension": "SN",
    },
    {"topic": "描述你做事时脑子里会同时跑多少个不同的想法？", "dimension": "SN"},
    {
        "topic": "你有没有特别关注某些别人不太注意的细节？具体是什么？",
        "dimension": "SN",
    },
    {"topic": "如果让你设计一个全新的系统，你会从什么角度切入？", "dimension": "SN"},
    {
        "topic": "你会不会反复验证已经确定的事实，还是相信一次就够了？",
        "dimension": "SN",
    },
    # TF 维度 - 思考/情感
    {
        "topic": "你做重要决定时，是逻辑优先还是感受优先？哪个让你更舒服？",
        "dimension": "TF",
    },
    {"topic": "当你的理性分析和情感直觉冲突时，一般谁赢？", "dimension": "TF"},
    {"topic": "你觉得'对的事'和'让人舒服的事'是一样的吗？", "dimension": "TF"},
    {"topic": "你会不会为了照顾别人情绪而压下自己的真实判断？", "dimension": "TF"},
    {"topic": "你善于在冲突中保持中立吗？还是会偏向某一边？", "dimension": "TF"},
    {"topic": "有没有一件事你明知道'应该'怎么做，但就是做不到？", "dimension": "TF"},
    {"topic": "你更在意公平原则还是每个人的具体感受？", "dimension": "TF"},
    {"topic": "当朋友向你诉苦时，你倾向于给建议还是先共情？", "dimension": "TF"},
    {"topic": "你觉得效率和人情味在工作中怎么平衡？", "dimension": "TF"},
    {"topic": "你会不会主动表达感谢和欣赏？还是觉得做了就够了？", "dimension": "TF"},
    # JP 维度 - 判断/知觉
    {
        "topic": "你是那种提前把计划列好的人，还是喜欢随机应变的风格？",
        "dimension": "JP",
    },
    {"topic": "当计划被打乱时，你通常是什么反应？", "dimension": "JP"},
    {"topic": "你享受最后关头的紧迫感吗？还是宁愿一切提前就绪？", "dimension": "JP"},
    {"topic": "你房间或工作区的整洁度和条理性大概是什么水平？", "dimension": "JP"},
    {"topic": "你会在事情开始前就做很多准备工作，还是边做边调整？", "dimension": "JP"},
    {
        "topic": "当有多件待办事项同时压过来时，你优先处理的标准是什么？",
        "dimension": "JP",
    },
    {
        "topic": "你习惯按时赴约还是经常迟到或踩点？有没有为此困扰过？",
        "dimension": "JP",
    },
    {"topic": "你会不会列清单？清单对你来说是减压还是另一种压力？", "dimension": "JP"},
    {"topic": "面对一个开放性、没有标准答案的问题，你是什么状态？", "dimension": "JP"},
    {
        "topic": "你更倾向于把事情做完（哪怕有些粗糙）还是慢慢打磨到满意？",
        "dimension": "JP",
    },
]


# ---------------------------------------------------------------------------
# TopicGenerator
# ---------------------------------------------------------------------------


class TopicGenerator:
    """
    话题生成器。

    用法：
        tg = TopicGenerator()
        topic = tg.get_next(user_id="abc123", dimension="EI")
        # 返回 dict: {"topic": str, "dimension": str, "source": str}
    """

    def __init__(self, seed_path: str | Path | None = None):
        """
        Args:
            seed_path: 话题种子数据路径，默认读取 seed_data/scraped_topics.json
        """
        if seed_path is None:
            seed_path = (
                Path(__file__).parent.parent / "seed_data" / "scraped_topics.json"
            )
        self.seed_path = Path(seed_path)
        self._seed_topics: list[dict] = self._load_seed()

    # -------------------------------------------------------------------------
    # 初始化
    # -------------------------------------------------------------------------

    def _load_seed(self) -> list[dict]:
        """加载抓取的话题，初始化到数据库（首次运行）。"""
        if not self.seed_path.exists():
            return []

        with open(self.seed_path, encoding="utf-8") as f:
            raw = json.load(f)

        # 兼容不同格式
        topics = raw if isinstance(raw, list) else raw.get("topics", [])
        if not topics:
            return []

        # 写入话题池表（去重，由 db.upsert_topic 保证）
        for item in topics:
            topic_text = item.get("title") or item.get("topic", "")
            dimension = item.get("dimension", random.choice(["EI", "SN", "TF", "JP"]))
            source = "news"
            expires = item.get("expires_at") or self._default_expires()
            db.upsert_topic(topic_text, dimension, source, expires)

        return topics

    def _default_expires(self) -> str:
        """默认过期时间：当前时间 + 24 小时。"""
        exp = datetime.now(timezone.utc).timestamp() + 86400
        return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # 初始化内置话题池
    # -------------------------------------------------------------------------

    def seed_builtin_topics(self) -> int:
        """
        将内置话题写入数据库（去重）。

        Returns:
            写入的话题数量
        """
        count = 0
        for item in _BUILTIN_TOPICS:
            existing = db.get_valid_topics(dimension=item["dimension"], limit=100)
            if any(t["topic"] == item["topic"] for t in existing):
                continue
            db.upsert_topic(
                topic=item["topic"],
                dimension=item["dimension"],
                source="builtin",
                expires_at=None,  # 内置话题永不过期
            )
            count += 1
        return count

    # -------------------------------------------------------------------------
    # 核心接口
    # -------------------------------------------------------------------------

    def get_next(
        self,
        user_id: str,
        dimension: str | None = None,
        exclude_topics: list[str] | None = None,
    ) -> dict:
        """
        获取下一个话题。

        优先级：
            1. 数据库有效话题（先尝试指定维度）
            2. 其他维度有效话题
            3. 内置话题池（随机抽取）

        Args:
            user_id: 用户标识（用于去重历史）
            dimension: 优先维度，不指定则随机
            exclude_topics: 排除的话题列表（避免重复问同类）

        Returns:
            {"topic": str, "dimension": str, "source": str}
        """
        exclude_topics = exclude_topics or []
        history = db.get_conversation_history(user_id, limit=50)
        asked_topics = {log["topic"] for log in history} | set(exclude_topics)

        # 尝试获取数据库话题
        topics = self._get_db_topics(dimension=dimension, asked=asked_topics)
        if topics:
            chosen = random.choice(topics)
            return self._format_topic(chosen)

        # 降级：其他维度
        if dimension:
            topics = self._get_db_topics(dimension=None, asked=asked_topics)
            if topics:
                chosen = random.choice(topics)
                return self._format_topic(chosen)

        # 降级：内置话题
        return self._get_builtin_fallback(dimension=dimension, asked=asked_topics)

    def _get_db_topics(
        self,
        dimension: str | None,
        asked: set[str],
        limit: int = 50,
    ) -> list[dict]:
        """从数据库获取有效话题（过滤已问过）。"""
        topics = db.get_valid_topics(dimension=dimension, limit=limit)
        return [t for t in topics if t["topic"] not in asked]

    def _get_builtin_fallback(
        self,
        dimension: str | None,
        asked: set[str],
    ) -> dict:
        """内置话题兜底。"""
        pool = [t for t in _BUILTIN_TOPICS if t["topic"] not in asked]
        if dimension:
            pool = [t for t in pool if t["dimension"] == dimension]
        if not pool:
            pool = _BUILTIN_TOPICS  # 全用完了就循环用

        chosen = random.choice(pool)
        return self._format_topic(chosen)

    def _format_topic(self, row: dict) -> dict:
        return {
            "topic": row["topic"],
            "dimension": row["dimension"],
            "source": row["source"],
        }

    # -------------------------------------------------------------------------
    # LLM 动态生成（由调用方注入 prompt）
    # -------------------------------------------------------------------------

    @staticmethod
    def build_llm_prompt(
        user_id: str,
        history: list[dict],
        dimensions: dict,
        target_dimension: str | None = None,
    ) -> str:
        """
        构建 LLM 话题生成 prompt（供外部调用方传给 LLM API）。

        返回 prompt 字符串，LLM 返回 JSON 格式的话题。

        Args:
            user_id: 用户标识
            history: 对话历史
            dimensions: 当前四维分值 {"EI": 0.5, ...}
            target_dimension: 强制生成特定维度话题

        Returns:
            完整的 prompt 字符串
        """
        dim_map = {
            "EI": "外向/内向",
            "SN": "实感/直觉",
            "TF": "思考/情感",
            "JP": "判断/知觉",
        }
        history_lines = ""
        if history:
            for log in history[-5:]:
                dim = dim_map.get(log.get("dimension", ""), log["dimension"])
                history_lines += (
                    f"- [{dim}] {log['topic']}\n  用户: {log['user_response'][:60]}\n"
                )

        # 计算最需要探索的维度（分值最接近 0.5 的，即最不确定）
        dim_scores = {k: abs(v - 0.5) for k, v in dimensions.items()}
        uncertain_dims = sorted(dim_scores, key=dim_scores.get)

        target_line = (
            f"（优先探索维度：{dim_map.get(target_dimension, random.choice(uncertain_dims))}）"
            if target_dimension
            else ""
        )

        return f"""你是一个 MBTI 话题设计专家。请为用户生成下一个分析话题。

## 用户当前画像
- 四维打分：EI={dimensions.get("EI", 0.5):.0%} / SN={dimensions.get("SN", 0.5):.0%} / TF={dimensions.get("TF", 0.5):.0%} / JP={dimensions.get("JP", 0.5):.0%}
- 最不确定维度：{dim_map.get(uncertain_dims[0], "EI")}（分值最接近50%，需要深入探索）

## 最近对话 {target_line}
{history_lines or "（尚无对话记录）"}

## 要求
1. 生成 1 个自然、口语化的问题，避免生硬
2. 优先探索最不确定的维度
3. 避免与历史话题重复
4. 返回格式（只返回 JSON，不要其他内容）：
{{"topic": "你的问题内容", "dimension": "EI|SN|TF|JP"}}
"""

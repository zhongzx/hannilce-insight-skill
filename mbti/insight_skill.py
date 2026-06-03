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
import urllib.error
import urllib.request
from datetime import datetime, timezone

from mbti import db
from mbti.models import MBTIProfile, make_user_id
from mbti.quality_controller import QualityController
from mbti.session_manager import SessionManager
from mbti.topic_generator import TopicGenerator

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

_MBTI_TYPE_DESCRIPTIONS = {
    "INTJ": {
        "name": "建筑师",
        "traits": "- 战略思维者，拥有清晰的长远愿景\n- 独立、自主，信赖自己的判断\n- 对知识和能力的追求近乎苛刻\n- 善于发现系统中的漏洞和改进空间\n- 表达直接，不喜欢废话",
        "career": "- 适合：战略咨询、系统架构、科研、金融分析\n- 优势领域：长期规划、复杂问题解决",
        "relationship": "- 深度对话胜于浅层闲聊\n- 忠诚但以行动表达而非言语\n- 需要空间独处来恢复能量",
        "growth": "- 注意表达情感需求，避免过度理性化\n- 学会接受不完美的计划\n- 培养耐心，理解他人节奏不同",
    },
    "INTP": {
        "name": "逻辑学家",
        "traits": "- 极度理性，痴迷于逻辑和推理\n- 充满好奇心，追求知识和理解\n- 擅长抽象思维和理论构建\n- 独立思考，不盲从权威\n- 表达精确但有时忽略社交细节",
        "career": "- 适合：科研、编程、哲学、数据分析\n- 优势领域：理论创新、系统设计",
        "relationship": "- 重视智识上的连接\n- 表达感情含蓄，需要时间表达感受\n- 独处时最能展现真实自我",
        "growth": "- 将思考转化为行动\n- 注意日常事务和组织管理\n- 主动表达欣赏和感谢",
    },
    "ENTJ": {
        "name": "指挥官",
        "traits": "- 天生的领导者，敢于决策\n- 战略眼光，善于规划全局\n- 直接、果断，不拖泥带水\n- 自信且有说服力\n- 追求效率和成果",
        "career": "- 适合：企业管理、创业、法律、政治\n- 优势领域：战略决策、团队领导",
        "relationship": "- 沟通直接，有时忽略他人感受\n- 忠诚且愿意为重要的人付出\n- 需要能挑战自己的伴侣",
        "growth": "- 练习倾听而非主导\n- 关注他人情绪而非只看结果\n- 学会接受自己的脆弱",
    },
    "ENTP": {
        "name": "辩论家",
        "traits": "- 头脑风暴高手，点子层出不穷\n- 善于发现弱点和漏洞\n- 热爱辩论和智识挑战\n- 多才多艺，兴趣广泛\n- 不喜欢重复和例行公事",
        "career": "- 适合：创业、投资、咨询、媒体\n- 优势领域：创新、业务拓展",
        "relationship": "- 喜欢有趣的精神对话\n- 善于社交但深度有限\n- 需要能跟上思维节奏的伙伴",
        "growth": "- 将想法落地而非一直探索新点子\n- 培养专注力和耐心\n- 学会坚持做完事情",
    },
    "INFJ": {
        "name": "提倡者",
        "traits": "- 理想主义者，有坚定的价值观\n- 洞察力强，善于理解他人\n- 安静而有深度\n- 追求意义和使命\n- 有强烈的道德感",
        "career": "- 适合：心理咨询、教育、艺术、公益\n- 优势领域：帮助他人、创造性表达",
        "relationship": "- 深度连接的渴望者\n- 善于倾听，默默支持他人\n- 需要独处时间来充电",
        "growth": "- 学会说“不”，保护自己的能量\n- 接受不完美和妥协\n- 表达需求而非总是隐忍",
    },
    "INFP": {
        "name": "调停者",
        "traits": "- 理想主义者，充满热情\n- 敏感且富有同理心\n- 重视个人价值观和真实自我\n- 善于文字和艺术表达\n- 安静但内心世界丰富",
        "career": "- 适合：写作、艺术、心理咨询、教育\n- 优势领域：创作、调解、理解人性",
        "relationship": "- 追求灵魂层面的连接\n- 敏感细腻，需要被理解\n- 外表安静但内心炽热",
        "growth": "- 行动力较弱，需设定具体目标\n- 学会处理冲突而非回避\n- 接纳现实的限制",
    },
    "ENFJ": {
        "name": "主人公",
        "traits": "- 天生的激励者，有魅力\n- 善于理解和支持他人\n- 有领导力，激励他人成长\n- 富有理想，有使命感\n- 热情且富有感染力",
        "career": "- 适合：教育、管理、咨询、公益\n- 优势领域：团队领导、人才培养",
        "relationship": "- 关心他人需求，善于社交\n- 给予支持但有时忽略自己\n- 渴望被认可和欣赏",
        "growth": "- 学会设立界限\n- 不要过度承担他人责任\n- 给自己留出恢复时间",
    },
    "ENFP": {
        "name": "竞选者",
        "traits": "- 热情洋溢，充满活力\n- 点子多，想象力丰富\n- 善于社交，有感染力\n- 适应力强，喜欢变化\n- 追求可能性和新体验",
        "career": "- 适合：创意、市场、媒体、公关\n- 优势领域：创新、激励他人",
        "relationship": "- 热情浪漫，重视精神交流\n- 需要自由和空间\n- 善于发现他人的潜力",
        "growth": "- 专注力有限，需培养持续力\n- 学会处理细节和承诺\n- 不要害怕冲突和困难",
    },
    "ISTJ": {
        "name": "物流师",
        "traits": "- 负责任，可靠踏实\n- 注重细节，有组织性\n- 传统价值观的守护者\n- 做事有条理、有计划\n- 沉默寡言但信守承诺",
        "career": "- 适合：会计、法律、行政、工程\n- 优势领域：执行、流程优化",
        "relationship": "- 行动表达爱意\n- 稳定、忠诚、可靠\n- 不善言辞但默默付出",
        "growth": "- 接纳变化和新想法\n- 学会灵活处理问题\n- 表达情感而非压抑",
    },
    "ISFJ": {
        "name": "守卫者",
        "traits": "- 温暖、可靠、乐于助人\n- 注重传统和责任\n- 细心体贴，关注他人需求\n- 默默奉献，不求回报\n- 传统且有责任感",
        "career": "- 适合：护理、教育、行政、服务\n- 优势领域：支持他人、维护稳定",
        "relationship": "- 无条件的支持者和照顾者\n- 表达含蓄但行动证明一切\n- 重视长期关系的维护",
        "growth": "- 学会接受感谢而非只付出\n- 设立健康的界限\n- 相信自己的价值和需求",
    },
    "ESTJ": {
        "name": "总经理",
        "traits": "- 果断、有执行力\n- 善于组织和领导\n- 重视规则和效率\n- 直接务实，结果导向\n- 传统价值的维护者",
        "career": "- 适合：管理、法律、军事、行政\n- 优势领域：执行、运营、组织",
        "relationship": "- 保护和照顾家人\n- 直接但真诚\n- 重视承诺和责任",
        "growth": "- 学会欣赏不同的做事方式\n- 注意他人感受而非只看事实\n- 接受建议和批评",
    },
    "ESFJ": {
        "name": "执政官",
        "traits": "- 热情、社交、善于照顾人\n- 注重和谐和他人感受\n- 有责任感，乐于助人\n- 传统且有组织性\n- 喜欢被需要和认可",
        "career": "- 适合：护理、教育、销售、人力资源\n- 优势领域：团队协调、服务他人",
        "relationship": "- 付出型的伴侣和朋友\n- 重视仪式感和节日\n- 需要被感谢和认可",
        "growth": "- 学会说不\n- 不过度在意他人的看法\n- 照顾自己的需求",
    },
    "ISTP": {
        "name": "鉴赏家",
        "traits": "- 务实、动手能力强\n- 善于分析问题和解决\n- 独立、自主、灵活\n- 喜欢探索事物如何运作\n- 安静、内敛但行动派",
        "career": "- 适合：工程、技术、手工艺、军事\n- 优势领域：实际操作、问题解决",
        "relationship": "- 行动而非言语表达\n- 需要空间和自由\n- 深度连接需要时间",
        "growth": "- 表达情感和想法\n- 考虑长远影响\n- 培养耐心和承诺",
    },
    "ISFP": {
        "name": "探险家",
        "traits": "- 艺术感强，善于审美\n- 安静、温柔、有同理心\n- 活在当下，享受体验\n- 灵活、适应力强\n- 重视个人空间和自由",
        "career": "- 适合：艺术、设计、手工艺、音乐\n- 优势领域：创意、美感、体验",
        "relationship": "- 浪漫且细腻\n- 用行动表达爱\n- 需要空间和理解",
        "growth": "- 克服犹豫，勇敢行动\n- 学会应对批评\n- 表达想法和感受",
    },
    "ESTP": {
        "name": "企业家",
        "traits": "- 精力充沛，喜欢冒险\n- 善于即兴发挥\n- 务实、直接、有说服力\n- 享受当下的刺激\n- 社交能力强，人脉广",
        "career": "- 适合：销售、创业、金融、演艺\n- 优势领域：谈判、危机处理",
        "relationship": "- 热情有趣的生活伙伴\n- 需要新鲜感\n- 直接沟通，不喜欢绕弯子",
        "growth": "- 培养专注力和耐心\n- 考虑行动的长远影响\n- 学会倾听而非总在说话",
    },
    "ESFP": {
        "name": "表演者",
        "traits": "- 活泼开朗，社交达人\n- 享受关注，善于娱乐他人\n- 活在当下，及时行乐\n- 热情、有感染力\n- 实际且有审美",
        "career": "- 适合：表演、销售、市场、公关\n- 优势领域：展示、娱乐、连接",
        "relationship": "- 热情洋溢的伴侣\n- 喜欢庆祝和新鲜感\n- 需要肯定和关注",
        "growth": "- 学会计划和承诺\n- 面对困难不要逃避\n- 培养深度而非广度",
    },
}

# 默认类型描述（兜底）
_DEFAULT_TYPE_DESC = {
    "name": "待确定",
    "traits": "- 人格特征待分析\n- 需要更多对话数据",
    "career": "- 职业倾向待分析",
    "relationship": "- 人际风格待分析",
    "growth": "- 成长建议待分析",
}


def _call_openrouter_chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    timeout_seconds: float = 45.0,
) -> str | None:
    url = os.environ.get(
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1/chat/completions",
    )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return None

    choices = result.get("choices", [])
    if not choices:
        return None
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()


def _render_report(profile: MBTIProfile, round_count: int) -> str:
    """渲染 MBTI 分析报告。"""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        model = os.environ.get("OPENROUTER_MODEL", "openrouter/auto")
        history = db.get_conversation_history(profile.user_id, limit=50)
        history_lines = "\n".join(
            f"- Q: {item.get('topic', '')}\n  A: {item.get('user_response', '')}"
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
            "4) 只输出 Markdown，不要输出代码块外的解释\n\n"
            f"用户：{profile.name}\n"
            f"轮数：{round_count}\n"
            f"当前画像摘要：{json.dumps(profile.to_summary(), ensure_ascii=False)}\n\n"
            "对话记录：\n"
            f"{history_lines or '（无对话记录）'}\n"
        )
        content = _call_openrouter_chat_completion(
            api_key=api_key,
            model=model,
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

    mbti_type = profile.final_type or "XXXX"
    type_info = _MBTI_TYPE_DESCRIPTIONS.get(mbti_type, _DEFAULT_TYPE_DESC)

    def dim_info(dim_name: str, letter: str) -> tuple:
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
        type_fullname=type_info["name"],
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
        character_traits=type_info["traits"],
        career_tendencies=type_info["career"],
        relationship_style=type_info["relationship"],
        growth_suggestions=type_info["growth"],
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
        self._tg: TopicGenerator | None = None
        self._qc: QualityController | None = None
        self._current_topic: str | None = None
        self._current_dimension: str | None = None

    # -------------------------------------------------------------------------
    # 公共接口
    # -------------------------------------------------------------------------

    def init(self) -> None:
        """初始化子模块。"""
        db.init_db()
        self._sm = SessionManager()
        self._tg = TopicGenerator()
        self._qc = QualityController()

    def handle_trigger(
        self,
        user_name: str,
        timestamp_iso: str,
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
        ctx = self._sm.get_or_create(user_name, timestamp_iso)
        profile: MBTIProfile = ctx["profile"]
        is_new = ctx["is_new"]

        # 生成话题
        next_topic = self._tg.get_next(user_id=profile.user_id)
        topic = next_topic["topic"]
        dimension = next_topic["dimension"]
        self._current_topic = topic
        self._current_dimension = dimension

        # 构建唤醒上下文（新会话不返回，老会话返回）
        wakeup_context = ""
        if not is_new:
            wakeup_context = self._sm.build_wakeup_context(user_name, timestamp_iso)

        return {
            "type": "next_topic",
            "topic": topic,
            "dimension": dimension,
            "is_new": is_new,
            "profile": profile.to_summary(),
            "wakeup_context": wakeup_context,
        }

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
                "type": "next_topic" | "report",
                "topic": str,           # 下一话题（仅 next_topic）
                "dimension": str,       # 下一维度（仅 next_topic）
                "profile": dict,        # 当前画像摘要
                "quality": dict,        # 本轮评分详情
                "report": str,          # 分析报告（仅 report）
                "should_archive": bool, # 是否应结束话题
                "archive_reason": str,  # 结束原因
            }
        """
        if self._sm is None or self._qc is None or self._tg is None:
            self.init()

        user_id = make_user_id(user_name, timestamp_iso)

        # 1. 质量评估
        quality_result = self._qc.evaluate_round(
            user_id=user_id,
            topic=self._current_topic or "",
            user_response=user_response,
        )
        token_score = quality_result["token_score"]
        semantic_score = quality_result["semantic_score"]
        confidence = quality_result["confidence"]
        should_archive = quality_result["should_archive"]
        archive_reason = quality_result["archive_reason"]

        # 2. 记录对话
        self._sm.record_round(
            user_id=user_id,
            topic=self._current_topic or "",
            user_response=user_response,
            dimension=self._current_dimension or "",
            token_score=token_score,
            semantic_score=semantic_score,
            confidence=confidence,
        )

        # 3. 更新维度分值
        # 从用户回复中提取维度倾向（这里需要 LLM 辅助，后续优化）
        # 暂时用语义分数作为维度更新的参考信号
        profile = self._sm.update_dimensions(
            user_id=user_id,
            dimension=self._current_dimension or "EI",
            score=(semantic_score * 0.6 + token_score * 0.4),  # 综合评分更新维度
        )

        # 4. 检查是否应输出报告
        round_count = len(db.get_conversation_history(user_id, limit=100))
        report = None
        topic = None
        dimension = None

        if should_archive:
            # 生成报告
            report = _render_report(profile, round_count)
        else:
            # 获取下一个话题
            next_topic = self._tg.get_next(user_id=user_id)
            topic = next_topic["topic"]
            dimension = next_topic["dimension"]
            self._current_topic = topic
            self._current_dimension = dimension

        return {
            "type": "report" if should_archive else "next_topic",
            "topic": topic,
            "dimension": dimension,
            "profile": profile.to_summary(),
            "quality": {
                "token_score": token_score,
                "semantic_score": semantic_score,
                "confidence": confidence,
            },
            "report": report,
            "should_archive": should_archive,
            "archive_reason": archive_reason,
        }


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
        timestamp_iso = datetime.now(timezone.utc).isoformat()

    skill = InsightSkill()
    return skill.handle_trigger(user_name, timestamp_iso)


if __name__ == "__main__":
    # 简单测试
    import sys

    if len(sys.argv) > 1:
        name = sys.argv[1]
        result = run(name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("用法: python insight_skill.py <姓名>")

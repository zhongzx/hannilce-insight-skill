from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mbti.insight_skill import InsightSkill
from openrouter_client import load_openrouter_settings


@dataclass(frozen=True)
class SimulationConfig:
    scenario: str
    user_name: str
    session_id: str
    max_turns: int
    openrouter: str
    jsonl_out: str | None
    quality_check: bool
    random_profile: bool
    seed: int | None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _configure_openrouter(mode: str) -> None:
    if mode == "on":
        return
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_CONFIG"):
        os.environ.pop(key, None)
    os.environ["OPENROUTER_CONFIG"] = "/path/does-not-exist/.openrouter.json"


def _print_header(cfg: SimulationConfig) -> None:
    print("=" * 72)
    print("V0.3 对话回放模拟器")
    print(f"scenario={cfg.scenario} user={cfg.user_name}")
    print(f"session={cfg.session_id}")
    print(
        f"max_turns={cfg.max_turns} openrouter={cfg.openrouter} "
        f"quality_check={'on' if cfg.quality_check else 'off'}"
    )
    print("=" * 72)


def _render_assistant(result: dict[str, Any]) -> str:
    rtype = str(result.get("type") or "")
    if rtype == "report":
        report = result.get("report")
        if isinstance(report, str) and report.strip():
            return report.strip()
        return json.dumps(result, ensure_ascii=False, indent=2)

    if rtype == "summary":
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return json.dumps(result, ensure_ascii=False, indent=2)

    if rtype == "archive":
        message = result.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return json.dumps(result, ensure_ascii=False, indent=2)

    topic = result.get("topic")
    if isinstance(topic, str) and topic.strip():
        return topic.strip()

    return json.dumps(result, ensure_ascii=False, indent=2)


def _choose_profile_answer(
    *,
    scenario: str,
    assistant_text: str,
    outlier_birth_used: bool,
    random_profile: bool,
    rng: random.Random,
) -> str:
    if "确认就是这个" in assistant_text and "重新输入" in assistant_text:
        return "2" if scenario == "outlier_birth" else "0"
    if "YYYYMM" in assistant_text:
        if scenario == "outlier_birth":
            return "199603" if outlier_birth_used else "189603"
        if random_profile:
            now = datetime.now(UTC)
            age_years = rng.randint(18, 45)
            year = max(1850, now.year - age_years)
            month = rng.randint(1, 12)
            if year == now.year and month > now.month:
                month = max(1, now.month)
            return f"{year:04d}{month:02d}"
        if scenario == "happy":
            return "199803"
        return "0"
    if "技术/产品" in assistant_text and "运营/市场" in assistant_text:
        if random_profile:
            return str(rng.randint(1, 7))
        if scenario == "happy":
            return "1"
        if scenario == "outlier_birth":
            return "1"
        return "0"
    is_gender_prompt = (
        "男" in assistant_text
        and "女" in assistant_text
        and "直接回数字" in assistant_text
    )
    if is_gender_prompt:
        if random_profile:
            return str(rng.choice([1, 2, 3, 4]))
        if scenario == "happy":
            return "1"
        return "4"

    if scenario == "uncooperative":
        return "0"

    if scenario == "happy":
        return "0"

    return "0"


def _choose_chat_answer(*, scenario: str, turn_index: int) -> str:
    if scenario == "uncooperative":
        return "不知道。"

    if scenario == "nonsense":
        return "asdfqwer"

    if scenario == "report":
        return "/报告" if turn_index >= 2 else "最近挺忙的。"

    if scenario == "summary_deny":
        return "还好。"

    if scenario == "happy":
        samples = [
            "最近在忙一个项目，上头但也有点焦虑。",
            "我会先把最关键的风险点列出来，再一项项解决。",
            "压力大的时候我一般先自己消化一阵子，想清楚再找人聊。",
            "如果时间允许，我更喜欢把事情规划得清楚一点再开干。",
        ]
        return samples[turn_index % len(samples)]

    return "最近挺忙的。"


def _choose_summary_feedback(*, scenario: str) -> str:
    if scenario == "summary_deny":
        return "不太对。"
    if scenario == "summary_confirm":
        return "差不多。"
    if scenario == "report":
        return "/报告"
    return "嗯。"


def _should_force_summary(skill: InsightSkill, scenario: str) -> bool:
    return scenario in {"summary_deny", "summary_confirm"}


def _force_summary_state(skill: InsightSkill) -> None:
    skill._awaiting_summary_feedback = True
    skill._last_summary = (
        "和你聊下来，我有个不一定准的感觉："
        "你更倾向先自己消化，想清楚再说。你觉得我理解得对吗？哪里可能偏了？"
    )


def _log_jsonl(path: str | None, obj: dict[str, Any]) -> None:
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _collect_quality_warnings(
    *,
    assistant_text: str,
    result: dict[str, Any],
    previous_assistant_text: str | None,
) -> list[str]:
    warnings: list[str] = []
    cleaned = assistant_text.strip()
    if not cleaned:
        warnings.append("assistant 输出为空")
        return warnings

    rtype = str(result.get("type") or "")
    if rtype == "report":
        return warnings

    if "MBTI" in cleaned or "维度" in cleaned:
        warnings.append("assistant 文本出现 MBTI/维度字样")

    if re.search(r"你倾向于.+还是.+", cleaned):
        warnings.append("出现二选一问法：你倾向于 A 还是 B")
    if re.search(r"\bA\b.{0,10}\bB\b", cleaned) and "还是" in cleaned:
        warnings.append("疑似二选一/量表问法")

    if rtype == "summary" and not re.search(r"(你觉得呢|对吗|偏了|理解偏)", cleaned):
        warnings.append("summary 缺少邀请纠正/反馈的收尾")

    is_repeated = (
        previous_assistant_text is not None
        and cleaned == previous_assistant_text.strip()
    )
    if is_repeated:
        warnings.append("assistant 连续重复同一句话")

    return warnings


def run_simulation(cfg: SimulationConfig) -> int:
    _configure_openrouter(cfg.openrouter)
    _print_header(cfg)
    if cfg.openrouter == "on" and load_openrouter_settings() is None:
        print(
            "[warn]".ljust(12),
            "openrouter=on 但未检测到可用配置，将自动回退为无模型模式。",
        )

    rng = random.Random(cfg.seed)
    skill = InsightSkill()
    result = skill.handle_trigger(cfg.user_name, cfg.session_id)
    assistant_text = _render_assistant(result)
    print("[assistant]".ljust(12), assistant_text)
    _log_jsonl(
        cfg.jsonl_out,
        {
            "ts": _now_iso(),
            "role": "assistant",
            "payload": result,
        },
    )

    chat_turn_index = 0
    previous_assistant_text: str | None = None
    assistant_question_count = 0
    assistant_total_count = 0
    warning_counts: dict[str, int] = {}
    outlier_birth_used = False

    for i in range(cfg.max_turns):
        rtype = str(result.get("type") or "")
        topic_source = str(result.get("topic_source") or "")
        assistant_text = _render_assistant(result)

        if rtype == "report":
            print("[done]".ljust(12), "已生成报告，结束回放。")
            return 0
        if rtype == "archive":
            print("[done]".ljust(12), "会话已结束，结束回放。")
            return 0

        if rtype == "summary":
            user_text = _choose_summary_feedback(scenario=cfg.scenario)
        elif topic_source.startswith("collect_profile"):
            user_text = _choose_profile_answer(
                scenario=cfg.scenario,
                assistant_text=assistant_text,
                outlier_birth_used=outlier_birth_used,
                random_profile=cfg.random_profile
                and cfg.scenario not in {"outlier_birth", "uncooperative"},
                rng=rng,
            )
            if cfg.scenario == "outlier_birth" and user_text == "189603":
                outlier_birth_used = True
        else:
            user_text = _choose_chat_answer(
                scenario=cfg.scenario,
                turn_index=chat_turn_index,
            )
            chat_turn_index += 1

        if (
            _should_force_summary(skill, cfg.scenario)
            and chat_turn_index >= 3
            and not skill._awaiting_summary_feedback
        ):
            _force_summary_state(skill)

        print(f"[turn {i + 1}/{cfg.max_turns}]".ljust(12), "sending...")
        print("[user]".ljust(12), user_text)
        _log_jsonl(
            cfg.jsonl_out,
            {
                "ts": _now_iso(),
                "role": "user",
                "text": user_text,
            },
        )

        start = time.monotonic()
        result = skill.handle_response(
            cfg.user_name,
            cfg.session_id,
            user_text,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        assistant_text = _render_assistant(result)
        print("[assistant]".ljust(12), assistant_text)
        print("[elapsed_ms]".ljust(12), elapsed_ms)

        if cfg.quality_check:
            assistant_total_count += 1
            if "？" in assistant_text or "?" in assistant_text:
                assistant_question_count += 1
            warnings = _collect_quality_warnings(
                assistant_text=assistant_text,
                result=result,
                previous_assistant_text=previous_assistant_text,
            )
            for w in warnings:
                warning_counts[w] = warning_counts.get(w, 0) + 1
                print("[quality]".ljust(12), w)
            previous_assistant_text = assistant_text

        _log_jsonl(
            cfg.jsonl_out,
            {
                "ts": _now_iso(),
                "role": "assistant",
                "elapsed_ms": elapsed_ms,
                "payload": result,
            },
        )

    if cfg.quality_check and assistant_total_count > 0:
        ratio = assistant_question_count / assistant_total_count
        print("=" * 72)
        print("[quality]".ljust(12), f"assistant 问句占比={ratio:.2f}")
        if ratio > 0.9:
            print("[quality]".ljust(12), "问句占比偏高，可能像采访而非朋友聊天")
        if warning_counts:
            print("[quality]".ljust(12), "告警汇总：")
            for key in sorted(warning_counts.keys()):
                print("[quality]".ljust(12), f"{warning_counts[key]}x {key}")
        else:
            print("[quality]".ljust(12), "未发现规则层面的明显告警")

    print("[done]".ljust(12), "达到最大轮次，结束回放。")
    return 0


def _parse_args() -> SimulationConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        default="happy",
        choices=[
            "all",
            "happy",
            "uncooperative",
            "outlier_birth",
            "summary_deny",
            "summary_confirm",
            "report",
            "nonsense",
        ],
    )
    parser.add_argument("--user-name", default="模拟用户")
    parser.add_argument("--session-id", default=_now_iso())
    parser.add_argument("--max-turns", type=int, default=18)
    parser.add_argument("--openrouter", choices=["on", "off"], default="off")
    parser.add_argument("--jsonl-out", default=None)
    parser.add_argument("--quality-check", action="store_true")
    parser.add_argument("--random-profile", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    return SimulationConfig(
        scenario=str(args.scenario),
        user_name=str(args.user_name),
        session_id=str(args.session_id),
        max_turns=int(args.max_turns),
        openrouter=str(args.openrouter),
        jsonl_out=str(args.jsonl_out) if args.jsonl_out else None,
        quality_check=bool(args.quality_check),
        random_profile=bool(args.random_profile),
        seed=int(args.seed) if args.seed is not None else None,
    )


def main() -> int:
    cfg = _parse_args()
    if cfg.scenario != "all":
        return run_simulation(cfg)

    scenarios = [
        "happy",
        "uncooperative",
        "outlier_birth",
        "summary_confirm",
        "summary_deny",
        "report",
        "nonsense",
    ]
    exit_code = 0
    for s in scenarios:
        per_cfg = SimulationConfig(
            scenario=s,
            user_name=cfg.user_name,
            session_id=_now_iso(),
            max_turns=cfg.max_turns,
            openrouter=cfg.openrouter,
            jsonl_out=cfg.jsonl_out,
            quality_check=cfg.quality_check,
            random_profile=cfg.random_profile,
            seed=cfg.seed,
        )
        code = run_simulation(per_cfg)
        exit_code = exit_code or code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

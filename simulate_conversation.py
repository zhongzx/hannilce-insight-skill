from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mbti.insight_skill import InsightSkill


@dataclass(frozen=True)
class SimulationConfig:
    scenario: str
    user_name: str
    session_id: str
    max_turns: int
    openrouter: str
    jsonl_out: str | None


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
    print(f"max_turns={cfg.max_turns} openrouter={cfg.openrouter}")
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
) -> str:
    if "确认就是这个" in assistant_text and "重新输入" in assistant_text:
        return "2" if scenario == "outlier_birth" else "0"
    if "YYYYMM" in assistant_text:
        if scenario == "outlier_birth":
            return "199603" if outlier_birth_used else "189603"
        if scenario == "happy":
            return "199803"
        return "0"
    if "技术/产品" in assistant_text and "运营/市场" in assistant_text:
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


def run_simulation(cfg: SimulationConfig) -> int:
    _configure_openrouter(cfg.openrouter)
    _print_header(cfg)

    skill = InsightSkill()
    result = skill.handle_trigger(cfg.user_name, cfg.session_id)

    print("[assistant]".ljust(12), _render_assistant(result))
    _log_jsonl(
        cfg.jsonl_out,
        {
            "ts": _now_iso(),
            "role": "assistant",
            "payload": result,
        },
    )

    chat_turn_index = 0
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

        print("[assistant]".ljust(12), _render_assistant(result))
        print("[elapsed_ms]".ljust(12), elapsed_ms)

        _log_jsonl(
            cfg.jsonl_out,
            {
                "ts": _now_iso(),
                "role": "assistant",
                "elapsed_ms": elapsed_ms,
                "payload": result,
            },
        )

    print("[done]".ljust(12), "达到最大轮次，结束回放。")
    return 0


def _parse_args() -> SimulationConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        default="happy",
        choices=[
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
    args = parser.parse_args()

    return SimulationConfig(
        scenario=str(args.scenario),
        user_name=str(args.user_name),
        session_id=str(args.session_id),
        max_turns=int(args.max_turns),
        openrouter=str(args.openrouter),
        jsonl_out=str(args.jsonl_out) if args.jsonl_out else None,
    )


def main() -> int:
    cfg = _parse_args()
    return run_simulation(cfg)


if __name__ == "__main__":
    raise SystemExit(main())

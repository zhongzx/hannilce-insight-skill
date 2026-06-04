from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mbti import db
from mbti.insight_skill import InsightSkill
from mbti.models import MBTIProfile, make_user_id
from openrouter_client import load_openrouter_settings


@dataclass(frozen=True)
class SimulationConfig:
    scenario: str
    user_name: str
    session_id: str
    max_turns: int
    openrouter: str
    enable_llm_signals: bool
    enable_llm_semantic: bool
    enable_unified_llm: bool
    jsonl_out: str | None
    quality_check: bool
    random_profile: bool
    seed: int | None
    trace_profile: bool
    trace_metrics: bool
    summarize_jsonl: str | None
    ci_mode: bool


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_writable_db_path() -> str:
    existing = os.environ.get("MBTI_DB_PATH")
    if existing:
        return existing
    path = Path.cwd() / "artifacts" / "sim" / "db" / "sessions.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["MBTI_DB_PATH"] = str(path)
    return str(path)


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
    print(f"db={os.environ.get('MBTI_DB_PATH')}")
    print(
        f"max_turns={cfg.max_turns} openrouter={cfg.openrouter} "
        f"quality_check={'on' if cfg.quality_check else 'off'} "
        f"trace_profile={'on' if cfg.trace_profile else 'off'} "
        f"trace_metrics={'on' if cfg.trace_metrics else 'off'}"
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
            if year == now.year - 18 and month > now.month:
                month = now.month
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

    if scenario == "breeze":
        samples = [
            "最近还行，有点忙但能扛住。",
            "嗯，有点上头，也有点累。",
            "我一般先把要紧的事处理掉，别的先放放。",
            "说不上来，就是有点被推着走。",
            "如果能选，我更想把节奏放慢一点。",
            "其实也有开心的地方，只是没太顾得上感受。",
        ]
        return samples[turn_index % len(samples)]

    if scenario == "report":
        if turn_index >= 6:
            return "/报告"
        samples = [
            "最近挺忙的，但也还扛得住。",
            "工作上有点挤压感，脑子停不下来。",
            "我其实想把节奏放慢一点，但总有事推着走。",
            "最近情绪有点钝，没啥大起伏。",
            "我也说不上来具体怎么了，就是有点累。",
            "如果能选，我更想先把手头最要紧的事收口。",
        ]
        return samples[turn_index % len(samples)]

    if scenario == "summary_deny":
        samples = [
            "还好。",
            "一般吧。",
            "就那样。",
            "没啥特别的。",
            "最近挺忙的。",
        ]
        return samples[turn_index % len(samples)]

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


def _estimate_tokens(text: str) -> int:
    cleaned = text.strip()
    if not cleaned:
        return 0
    return max(1, int(len(cleaned) / 4))


def _print_profile_trace(*, user_id: str) -> None:
    row = db.get_profile(user_id)
    if not row:
        return
    profile = MBTIProfile.from_db_row(row)
    summary = profile.to_summary()
    print(
        "[profile]".ljust(12),
        json.dumps(summary, ensure_ascii=False),
        file=sys.stderr,
    )


def _print_metrics_trace(
    *,
    user_text: str,
    assistant_text: str,
    elapsed_ms: int,
) -> None:
    metrics = {
        "elapsed_ms": elapsed_ms,
        "user_chars": len(user_text),
        "assistant_chars": len(assistant_text),
        "user_tokens_est": _estimate_tokens(user_text),
        "assistant_tokens_est": _estimate_tokens(assistant_text),
    }
    print(
        "[metrics]".ljust(12),
        json.dumps(metrics, ensure_ascii=False),
        file=sys.stderr,
    )


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
    os.environ["MBTI_ENABLE_LLM_SIGNALS"] = "1" if cfg.enable_llm_signals else "0"
    os.environ["MBTI_ENABLE_LLM_SEMANTIC"] = "1" if cfg.enable_llm_semantic else "0"
    os.environ["MBTI_ENABLE_UNIFIED_TURN"] = "1" if cfg.enable_unified_llm else "0"
    _print_header(cfg)
    if cfg.openrouter == "on" and load_openrouter_settings() is None:
        print(
            "[warn]".ljust(12),
            "openrouter=on 但未检测到可用配置，将自动回退为无模型模式。",
        )

    rng = random.Random(cfg.seed)
    skill = InsightSkill()
    user_id = make_user_id(cfg.user_name, cfg.session_id)
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
    fatal_warnings = {
        "assistant 输出为空",
        "assistant 文本出现 MBTI/维度字样",
        "summary 缺少邀请纠正/反馈的收尾",
    }
    had_fatal_issue = False

    for i in range(cfg.max_turns):
        rtype = str(result.get("type") or "")
        topic_source = str(result.get("topic_source") or "")
        assistant_text = _render_assistant(result)

        if cfg.ci_mode and rtype == "error":
            print("[ci]".ljust(12), "出现 error 类型输出，判定失败。", file=sys.stderr)
            return 1

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
        if cfg.trace_metrics:
            _print_metrics_trace(
                user_text=user_text,
                assistant_text=assistant_text,
                elapsed_ms=elapsed_ms,
            )
        if cfg.trace_profile:
            _print_profile_trace(user_id=user_id)

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
                if cfg.ci_mode and w in fatal_warnings:
                    had_fatal_issue = True
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
    if cfg.ci_mode and had_fatal_issue:
        print(
            "[ci]".ljust(12),
            "出现致命告警（空输出/泄露内部词/summary 结构不合格），判定失败。",
            file=sys.stderr,
        )
        return 1
    return 0


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(len(xs) - 1, f + 1)
    if f == c:
        return xs[f]
    lower = xs[f] * (c - k)
    upper = xs[c] * (k - f)
    return int(round(lower + upper))


def summarize_jsonl(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print("[error]".ljust(12), f"找不到文件：{path}", file=sys.stderr)
        return 2

    runs = 0
    assistant_messages = 0
    assistant_with_elapsed = 0
    user_messages = 0

    type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    elapsed_all: list[int] = []
    elapsed_openrouter: list[int] = []
    elapsed_collect_profile: list[int] = []

    topic_count = 0
    question_topic_count = 0
    repeat_topics = 0
    previous_topic: str | None = None

    with p.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = obj.get("role")
            if role == "user":
                user_messages += 1
                continue
            if role != "assistant":
                continue

            assistant_messages += 1
            payload = obj.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            if payload.get("is_new") is True:
                runs += 1

            rtype = str(payload.get("type") or "")
            type_counts[rtype] = type_counts.get(rtype, 0) + 1

            source = str(payload.get("topic_source") or "")
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

            elapsed_ms = obj.get("elapsed_ms")
            if isinstance(elapsed_ms, int):
                assistant_with_elapsed += 1
                elapsed_all.append(elapsed_ms)
                if source.startswith("collect_profile"):
                    elapsed_collect_profile.append(elapsed_ms)
                elif source == "openrouter":
                    elapsed_openrouter.append(elapsed_ms)

            topic = payload.get("topic")
            if isinstance(topic, str) and topic.strip():
                t = topic.strip()
                topic_count += 1
                if "?" in t or "？" in t:
                    question_topic_count += 1
                if previous_topic is not None and t == previous_topic:
                    repeat_topics += 1
                previous_topic = t

    print("=" * 72)
    print("JSONL 汇总")
    print(f"path={path}")
    print(f"runs={runs}")
    print(
        f"assistant_messages={assistant_messages} "
        f"(with_elapsed={assistant_with_elapsed})"
    )
    print(f"user_messages={user_messages}")
    print(f"types={json.dumps(type_counts, ensure_ascii=False)}")
    print(f"sources={json.dumps(source_counts, ensure_ascii=False)}")
    topic_question_ratio = question_topic_count / max(1, topic_count)
    print(f"topic_question_ratio={topic_question_ratio:.4f}")
    print(f"topic_count={topic_count}")
    print(f"topic_repeats={repeat_topics}")

    def _print_latency(name: str, xs: list[int]) -> None:
        if not xs:
            print(f"{name}: count=0")
            return
        avg = int(round(sum(xs) / len(xs)))
        print(
            f"{name}: count={len(xs)} avg_ms={avg} "
            f"p50={_percentile(xs, 0.5)} p90={_percentile(xs, 0.9)} "
            f"p99={_percentile(xs, 0.99)} max={max(xs)}"
        )

    _print_latency("elapsed_openrouter", elapsed_openrouter)
    _print_latency("elapsed_collect_profile", elapsed_collect_profile)
    _print_latency("elapsed_all", elapsed_all)
    print("=" * 72)
    return 0


def _parse_args() -> SimulationConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summarize-jsonl", default=None)
    parser.add_argument(
        "--scenario",
        default="happy",
        choices=[
            "all",
            "happy",
            "breeze",
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
    parser.add_argument("--llm-signals", choices=["on", "off"], default="on")
    parser.add_argument("--llm-semantic", choices=["on", "off"], default="on")
    parser.add_argument("--unified-llm", choices=["on", "off"], default="off")
    parser.add_argument("--jsonl-out", default=None)
    parser.add_argument("--quality-check", action="store_true")
    parser.add_argument("--random-profile", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--trace-profile", action="store_true")
    parser.add_argument("--trace-metrics", action="store_true")
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args()

    return SimulationConfig(
        scenario=str(args.scenario),
        user_name=str(args.user_name),
        session_id=str(args.session_id),
        max_turns=int(args.max_turns),
        openrouter=str(args.openrouter),
        enable_llm_signals=str(args.llm_signals) == "on",
        enable_llm_semantic=str(args.llm_semantic) == "on",
        enable_unified_llm=str(args.unified_llm) == "on",
        jsonl_out=str(args.jsonl_out) if args.jsonl_out else None,
        quality_check=bool(args.quality_check),
        random_profile=bool(args.random_profile),
        seed=int(args.seed) if args.seed is not None else None,
        trace_profile=bool(args.trace_profile),
        trace_metrics=bool(args.trace_metrics),
        summarize_jsonl=(str(args.summarize_jsonl) if args.summarize_jsonl else None),
        ci_mode=bool(args.ci),
    )


def main() -> int:
    cfg = _parse_args()
    _ensure_writable_db_path()
    if cfg.summarize_jsonl:
        return summarize_jsonl(cfg.summarize_jsonl)
    if cfg.scenario != "all":
        return run_simulation(cfg)

    scenarios = [
        "happy",
        "breeze",
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
            enable_llm_signals=cfg.enable_llm_signals,
            enable_llm_semantic=cfg.enable_llm_semantic,
            enable_unified_llm=cfg.enable_unified_llm,
            jsonl_out=cfg.jsonl_out,
            quality_check=cfg.quality_check,
            random_profile=cfg.random_profile,
            seed=cfg.seed,
            trace_profile=cfg.trace_profile,
            trace_metrics=cfg.trace_metrics,
            summarize_jsonl=None,
            ci_mode=cfg.ci_mode,
        )
        code = run_simulation(per_cfg)
        exit_code = exit_code or code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

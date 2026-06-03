from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenRouterSettings:
    api_key: str
    model: str
    base_url: str


def load_openrouter_settings() -> OpenRouterSettings | None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL")
    base_url = os.environ.get(
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1/chat/completions",
    )

    if api_key and model:
        return OpenRouterSettings(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )

    config_path = os.environ.get("OPENROUTER_CONFIG")
    candidate = Path(config_path) if config_path else Path.cwd() / ".openrouter.json"

    if not candidate.exists():
        return None

    try:
        raw = candidate.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None

    api_key_from_file = data.get("api_key")
    model_from_file = data.get("model")
    base_url_from_file = data.get("base_url", base_url)
    if not api_key_from_file or not model_from_file:
        return None

    return OpenRouterSettings(
        api_key=str(api_key_from_file),
        model=str(model_from_file),
        base_url=str(base_url_from_file),
    )


def call_chat_completion(
    *,
    settings: OpenRouterSettings,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    timeout_seconds: float = 45.0,
) -> str | None:
    debug_enabled = os.environ.get("OPENROUTER_DEBUG") == "1"
    start_time = time.monotonic()
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url=settings.base_url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            raw = response.read().decode("utf-8")
            if debug_enabled:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                print(
                    f"[OPENROUTER] ok model={settings.model} "
                    f"status={getattr(response, 'status', 'unknown')} "
                    f"elapsed_ms={elapsed_ms}",
                    file=sys.stderr,
                )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        if debug_enabled:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            print(
                f"[OPENROUTER] failed model={settings.model} elapsed_ms={elapsed_ms}",
                file=sys.stderr,
            )
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


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def extract_first_json_object(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

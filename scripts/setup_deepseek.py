from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        char = text[i]
        if in_string:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
                out.append(char)
            elif char == "/" and i + 1 < len(text) and text[i + 1] == "/":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            elif char == "/" and i + 1 < len(text) and text[i + 1] == "*":
                i += 2
                while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
            else:
                out.append(char)
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonc":
        text = strip_jsonc(text)
    return json.loads(text)


def iter_candidates(config: dict):
    providers = config.get("providers") or config.get("provider") or {}
    if not isinstance(providers, dict):
        return
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        settings = provider.get("settings") or provider.get("options") or {}
        base = settings.get("baseURL") or settings.get("baseUrl") or ""
        key = settings.get("apiKey") or settings.get("api_key") or ""
        models = provider.get("models") or {}
        if isinstance(models, dict):
            model_names = list(models.keys())
        else:
            model_names = []
        for model in model_names:
            if "deepseek" in model.lower() or "deepseek" in name.lower():
                yield name, base, key, model


def resolve_key(key: str) -> str:
    if not isinstance(key, str):
        return ""
    match = re.fullmatch(r"\$\{env:([^}]+)\}", key.strip())
    if match:
        return os.getenv(match.group(1), "")
    return key


def test_candidate(base: str, key: str, model: str) -> tuple[bool, str]:
    if not base or not key:
        return False, "нет baseURL или apiKey"
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Ответь ровно одним словом: ок"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=40)
    except Exception as exc:  # noqa: BLE001
        return False, f"сетевая ошибка: {exc}"
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        return False, f"неожиданный ответ: {exc}"
    return True, str(content)[:80]


def update_env(values: dict[str, str]) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    keys = set(values)
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            name = line.split("=", 1)[0].strip()
            if name in keys:
                result.append(f"{name}={values[name]}")
                seen.add(name)
                continue
        result.append(line)
    for name in keys - seen:
        result.append(f"{name}={values[name]}")
    ENV_PATH.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    config_paths = [
        Path(os.path.expanduser("~/.config/opencode/opencode.jsonc")),
        Path(os.path.expanduser("~/.config/opencode/opencode.json")),
    ]
    candidates = []
    for path in config_paths:
        if not path.exists():
            continue
        try:
            config = load_config(path)
        except Exception as exc:  # noqa: BLE001
            print(f"Не удалось прочитать {path.name}: {exc}")
            continue
        for candidate in iter_candidates(config):
            candidates.append(candidate)

    # Сначала flash-модели, потом остальные.
    candidates.sort(key=lambda item: (0 if "flash" in item[3].lower() else 1, item[0], item[3]))
    seen = set()
    for name, base, key, model in candidates:
        signature = (base, model)
        if signature in seen:
            continue
        seen.add(signature)
        key = resolve_key(key)
        print(f"Проверяю: provider={name} model={model} base={base}")
        ok, message = test_candidate(base, key, model)
        if ok:
            update_env(
                {
                    "DEEPSEEK_BASE_URL": base,
                    "DEEPSEEK_API_KEY": key,
                    "DEEPSEEK_MODEL": model,
                }
            )
            print(f"OK: DeepSeek доступен. Ответ: {message}")
            print("Настройки записаны в .env (ключ не выводится).")
            return 0
        print(f"  не подошло: {message}")

    print("Не удалось найти рабочий DeepSeek-провайдер в конфиге OpenCode.")
    print("Добавьте в .env вручную: DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

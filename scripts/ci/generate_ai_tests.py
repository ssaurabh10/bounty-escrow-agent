import json
import os
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _collect_bounty_examples() -> list[dict[str, Any]]:
    example_file = ROOT / "oracle" / "backend_bounty_example.json"
    if not example_file.exists():
        return []
    try:
        data = json.loads(example_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [data] if isinstance(data, dict) else []


def _fallback_cases() -> dict[str, Any]:
    return {
        "source": "fallback",
        "tests": [
            {
                "name": "Health endpoint is up",
                "method": "GET",
                "path": "/health",
                "expected_status": 200,
            },
            {
                "name": "Protected admin endpoint blocks anonymous access",
                "method": "POST",
                "path": "/menu",
                "expected_status": 401,
            },
            {
                "name": "Order endpoint validates payload",
                "method": "POST",
                "path": "/orders",
                "expected_status": 400,
            },
        ],
        "note": "OPENAI_API_KEY not configured or AI call failed. Generated deterministic test cases.",
    }


def _ai_generate(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    prompt = (
        "Generate concise backend API test cases in JSON. "
        "Return only JSON with key 'tests' as an array of objects: "
        "{name, method, path, expected_status}. No markdown."
    )
    request_body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": "You are a backend QA generator."},
            {"role": "user", "content": f"{prompt}\n\nInput:\n{json.dumps(payload, ensure_ascii=True)}"},
        ],
        "temperature": 0.2,
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=request_body,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    tests = parsed.get("tests", [])
    if not isinstance(tests, list) or not tests:
        raise ValueError("AI response did not include tests array")
    return {"source": "ai", "tests": tests}


def _to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "### AI Test Case Generator",
        f"- Source: **{result.get('source', 'unknown')}**",
    ]
    if result.get("note"):
        lines.append(f"- Note: {result['note']}")
    lines.append("")
    for idx, test in enumerate(result.get("tests", []), start=1):
        lines.append(
            f"{idx}. `{test.get('method', 'GET')} {test.get('path', '/')}` "
            f"-> expected `{test.get('expected_status', 'n/a')}` | {test.get('name', 'Unnamed test')}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = {"bounties": _collect_bounty_examples()}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    try:
        result = _ai_generate(payload, api_key) if api_key else _fallback_cases()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result = _fallback_cases()
        result["note"] = f"AI generation failed: {exc}. Fallback tests produced."

    (REPORTS_DIR / "ai_tests.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (REPORTS_DIR / "ai_tests.md").write_text(_to_markdown(result), encoding="utf-8")
    print("Generated reports/ai_tests.json and reports/ai_tests.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

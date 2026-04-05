import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _step_state(name: str) -> str:
    value = os.getenv(name, "skipped").strip().lower()
    return value if value in {"success", "failure", "cancelled", "skipped"} else "skipped"


def _emoji(state: str) -> str:
    return {
        "success": "✅",
        "failure": "❌",
        "cancelled": "⚪",
        "skipped": "⚪",
    }.get(state, "⚪")


def main() -> int:
    states = {
        "flake8": _step_state("FLAKE8_OUTCOME"),
        "pylint": _step_state("PYLINT_OUTCOME"),
        "mypy": _step_state("MYPY_OUTCOME"),
        "pytest": _step_state("PYTEST_OUTCOME"),
        "security": _step_state("SECURITY_OUTCOME"),
        "ai_tests": _step_state("AI_TESTS_OUTCOME"),
    }
    failing_gates = [name for name in ("flake8", "pylint", "mypy", "pytest", "security") if states[name] != "success"]
    verdict = "PASS" if not failing_gates else "FAIL"

    payload = {
        "verdict": verdict,
        "failing_gates": failing_gates,
        "states": states,
    }
    (REPORTS_DIR / "verdict.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "## CI Bot Verdict",
        "",
        f"**Final verdict:** `{verdict}`",
        "",
        "### Checks",
    ]
    for key in ("flake8", "pylint", "mypy", "pytest", "security", "ai_tests"):
        lines.append(f"- {_emoji(states[key])} `{key}` -> `{states[key]}`")

    if failing_gates:
        lines.extend(
            [
                "",
                "### Action Needed",
                "- Fix failing quality/security gates before merge.",
                "- AI test generation is advisory; quality/security gates decide final verdict.",
            ]
        )
    else:
        lines.extend(["", "All quality and security gates passed."])

    (REPORTS_DIR / "verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Verdict: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

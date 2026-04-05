import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

TEXT_FILE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".env",
    ".txt",
    ".html",
}

IGNORE_DIRS = {".git", ".pytest_cache", "__pycache__", ".tmp_ref_site", "New Logic"}

OPENAI_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE)
HARDCODED_ASSIGN_PATTERN = re.compile(
    r"(OPENAI_API_KEY|API_KEY|SECRET|TOKEN)\s*[:=]\s*[\"']([^\"']{8,})[\"']",
    re.IGNORECASE,
)


def _is_candidate(path: Path) -> bool:
    if any(part in IGNORE_DIRS for part in path.parts):
        return False
    return path.suffix.lower() in TEXT_FILE_EXTS


def main() -> int:
    findings: list[dict[str, str]] = []
    for file_path in ROOT.rglob("*"):
        if not file_path.is_file() or not _is_candidate(file_path):
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if OPENAI_KEY_PATTERN.search(text):
            findings.append(
                {
                    "rule": "openai_api_key_literal",
                    "file": str(file_path.relative_to(ROOT)),
                    "message": "Potential secret detected. Use GitHub Secrets, not literals.",
                }
            )

        for match in HARDCODED_ASSIGN_PATTERN.finditer(text):
            value = match.group(2).strip()
            # Allow obvious local placeholders used by LocalNet setup.
            if value in {"", "changeme", "test", "demo"}:
                continue
            if len(set(value)) == 1:
                continue
            findings.append(
                {
                    "rule": "hardcoded_secret_assignment",
                    "file": str(file_path.relative_to(ROOT)),
                    "message": "Potential secret detected. Use GitHub Secrets, not literals.",
                }
            )
            break

    summary = {"ok": len(findings) == 0, "findings": findings}
    (REPORTS_DIR / "security.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if findings:
        print("Security reality check failed: hardcoded secret patterns were found.")
        for finding in findings:
            print(f"- {finding['file']} [{finding['rule']}]")
        return 1

    print("Security reality check passed: no hardcoded secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

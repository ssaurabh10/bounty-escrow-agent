from __future__ import annotations

from pathlib import Path
import json

from oracle.oracle_runner import evaluate_backend_free_stack, sha256_string


REPO_ROOT = Path(__file__).resolve().parent.parent


def read_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def read_json(path: str) -> dict:
    return json.loads(read_text(path))


def test_backend_bounty_schema_example_shape():
    schema = read_json("oracle/backend_bounty_schema.json")
    example = read_json("oracle/backend_bounty_example.json")

    required = set(schema["required"])
    assert required.issubset(example.keys())
    assert example["creator_language"] == "plain_business"
    assert example["delivery_mode"]["deployed_api_required"] is True
    assert example["acceptance_expectations"]["json_api"] is True
    assert len(example["acceptance_expectations"]["core_entities"]) >= 1


def test_backend_free_stack_partial_success_example():
    spec = read_text("oracle/backend_openapi_example.json")
    newman = read_text("oracle/backend_newman_report_example.json")
    schemathesis = read_text("oracle/backend_schemathesis_report_example.json")

    result = evaluate_backend_free_stack(
        spec,
        sha256_string(spec),
        newman,
        schemathesis,
    )

    assert result["verdict"] == "PARTIAL_SUCCESS"
    assert result["spec_hash_ok"] is True
    assert any("Newman failed assertions" in check for check in result["checks"])


def test_backend_free_stack_ambiguous_spec_for_too_few_paths():
    ambiguous_spec = """
    {
      "openapi": "3.0.3",
      "info": { "title": "Too vague", "version": "1.0.0" },
      "paths": {
        "/health": { "get": { "responses": { "200": { "description": "ok" } } } },
        "/login": { "post": { "responses": { "200": { "description": "ok" } } } }
      },
      "components": {}
    }
    """.strip()

    result = evaluate_backend_free_stack(
        ambiguous_spec,
        sha256_string(ambiguous_spec),
        '{"run":{"stats":{"assertions":{"total":2,"failed":0}},"failures":[]}}',
        '{"summary":{"test_cases":4,"failed_count":0,"errored_count":0}}',
    )

    assert result["verdict"] == "AMBIGUOUS_SPEC"


def test_backend_free_stack_creator_win_on_hash_mismatch():
    spec = read_text("oracle/backend_openapi_example.json")
    result = evaluate_backend_free_stack(
        spec,
        "deadbeef",
        '{"run":{"stats":{"assertions":{"total":2,"failed":0}},"failures":[]}}',
        '{"summary":{"test_cases":4,"failed_count":0,"errored_count":0}}',
    )

    assert result["verdict"] == "CREATOR_WIN"
    assert result["spec_hash_ok"] is False

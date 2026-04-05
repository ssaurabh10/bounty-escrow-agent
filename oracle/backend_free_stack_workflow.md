# Free Backend Dispute Stack

This is the fully free backend-dispute path for `Bounty Escrow Agent`.

## Goal

Let a non-technical creator post a backend bounty, then resolve disputes with:

- OpenAPI
- Postman + Newman
- Schemathesis

without requiring paid monitoring products.

## Generic backend intake

The creator only answers business questions in plain language. The platform then converts those answers into:

1. a frozen OpenAPI contract
2. a Postman collection
3. a Newman report
4. a Schemathesis report

The oracle combines those artifacts into a verdict.

## Files

- `backend_bounty_schema.json`
- `backend_bounty_example.json`
- `backend_openapi_example.json`
- `backend_postman_collection_example.json`
- `backend_newman_report_example.json`
- `backend_schemathesis_report_example.json`

## Oracle command

Compute the frozen OpenAPI hash first:

```powershell
$spec = Get-Content 'oracle/backend_openapi_example.json' -Raw
$hash = [System.BitConverter]::ToString(([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($spec)))).Replace('-','').ToLower()
python oracle/oracle_runner.py --backend-free oracle/backend_openapi_example.json oracle/backend_newman_report_example.json oracle/backend_schemathesis_report_example.json $hash
```

## Verdict logic

- `CONTRIBUTOR_WIN`
  The backend satisfies the frozen contract and free-stack checks.

- `PARTIAL_SUCCESS`
  Core flows pass, but some gaps remain.

- `CREATOR_WIN`
  Critical automated checks fail.

- `AMBIGUOUS_SPEC`
  The frozen backend spec is too vague to resolve securely.

## Why the schema matters

`backend_bounty_schema.json` is the reusable intake contract for non-technical creators.

It captures:

- business goal
- delivery mode
- feature toggles
- acceptance expectations

without requiring the creator to write OpenAPI, Postman, or schema rules by hand.

`backend_bounty_example.json` shows one generic business backend request using that schema.

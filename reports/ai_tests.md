### AI Test Case Generator
- Source: **fallback**
- Note: OPENAI_API_KEY not configured or AI call failed. Generated deterministic test cases.

1. `GET /health` -> expected `200` | Health endpoint is up
2. `POST /menu` -> expected `401` | Protected admin endpoint blocks anonymous access
3. `POST /orders` -> expected `400` | Order endpoint validates payload

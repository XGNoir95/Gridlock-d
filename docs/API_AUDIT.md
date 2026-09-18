# API security audit

Scope: the two official public endpoints implemented in `app/main.py`. The service is intentionally unauthenticated because the judging contract forbids login or manual access gates. It stores no user, tenant, billing, or campus data.

## Endpoint inventory

| Endpoint | Disposition | Checks |
|---|---|---|
| `GET /health` | Audited — clean | Exact fixed response, no dependency/provider call, no input, no secret exposure |
| `POST /optimize-energy` | Audited — clean | Strict DTOs, fixed 24-hour/1–3-note shape, unknown fields rejected, model output guardrailed, generic failures |

Framework documentation routes are disabled; the route-inventory test asserts that only these two paths exist.

## OWASP API Security Top 10 disposition

- **API1 BOLA:** N/A. No stored object lookup or ownership boundary.
- **API2 Broken Authentication:** N/A by official contract. No authentication endpoint, token, cookie, or session exists.
- **API3 Property Authorization / Exposure:** Clean. Request and response Pydantic models forbid unknown fields; responses contain only official fields.
- **API4 Resource Consumption:** Addressed. Valid input has exactly 24 hours and 1–3 notes; invalid structure is rejected before any model call; model attempts are capped at two; provider and LP calls have bounded time.
- **API5 Function Authorization:** N/A. There are no privileged functions or sister routes.
- **API6 Sensitive Business Flow:** N/A. The service processes synthetic challenge scenarios only.
- **API7 SSRF:** Clean. No user-supplied URL or outbound destination is accepted.
- **API8 Misconfiguration:** Clean. Debug/documentation routes are disabled, errors are generic, and logs record exception types only.
- **API9 Inventory:** Clean. Automated route inventory matches the official endpoint pair.
- **API10 Unsafe Upstream Consumption:** Clean. Provider output uses strict structured parsing followed by independent semantic guardrails before it can influence optimization.

## Runtime evidence

- `GET /health` returned HTTP 200 with `{"status":"ok"}`.
- `/docs` returned 404.
- an invalid optimization body returned controlled HTTP 400 without calling the model in integration tests.
- failed model guardrails made exactly one repair attempt and returned controlled HTTP 500.
- a primary outage plus backup failure path remained capped at two attempts.
- Gemini and Groq pool wraparound, blank/missing slots, concurrent selection, cooldown skipping, and secret-redacted error/representation behavior passed deterministic tests.
- Gemini schema/guardrail failure used one Gemini repair and never Groq; Gemini provider failure used one Groq attempt and never a third call.

No security finding is deferred or accepted. Live semantic qualification passed 60/60 for `gemini-3.5-flash-lite` (median 1.405 s, max 2.226 s) and 60/60 for Groq `openai/gpt-oss-120b` (median 1.855 s, max 4.812 s), using paced calls to avoid turning qualification into a quota stress test. All ten official public samples subsequently passed through both the local service and the Dockerized real-provider API, including directive comparison, optimization, serialized replay, aggregate verification, and reference-cost comparison. Public-network reachability remains pending.

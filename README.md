# Gridlock-d

GridWise LLM-assisted 24-hour energy scheduling service for the BUP CSE Fest 2026 preliminary round. It converts 1–3 natural-language operator notes into validated directives and returns a valid minimum-cost 24-hour energy plan.

The exact service contract is:

- `GET /health`
- `POST /optimize-energy`

## Architecture

```text
request schema
  -> Gemini primary (8-key round robin)
       -> on provider failure only: Groq backup (12-key round robin)
  -> common internal envelope + normalization trace
  -> deterministic shape/arithmetic/time guardrails
  -> per-hour directive constraints
  -> SciPy/HiGHS linear program
  -> serialized plan mapping
  -> independent hour-by-hour replay
  -> exact JSON response
```

The LLM is used only for semantic interpretation of `operator_notes`. Its exact structured result is used to create optimizer constraints. Deterministic code validates note coverage, types, adjustment shapes, hours, factor/reserve/cap arithmetic, and `no_op` semantics. It never replaces failed model output with a fabricated directive.

The optimizer uses grid import, solar used, signed battery flow, and battery state variables. The final validator independently checks all directives, energy balance, solar availability, battery bounds/rates/transitions, end-of-day neutrality, and recalculated aggregates.

## Requirements

- Python 3.12 recommended (3.11–3.12 supported)
- qualified Gemini and optional Groq models that support JSON-schema output
- Docker only for the container path

Runtime dependencies are pinned in `requirements.lock`; development dependencies are pinned in `requirements-dev.lock`.

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Required | Meaning |
|---|---:|---|
| `GEMINI_MODEL` | yes | Configurable Gemini primary model identifier |
| `GEMINI_API_KEY_1` … `GEMINI_API_KEY_8` | at least one | Ordered primary credential pool; blanks are ignored |
| `GROQ_MODEL` | only with backup | Configurable Groq backup model identifier |
| `GROQ_API_KEY_1` … `GROQ_API_KEY_12` | only with backup | Ordered backup credential pool; blanks are ignored |
| `LLM_TIMEOUT_SECONDS` | no | Per-provider-attempt timeout; default `4` |
| `LLM_HARD_REQUEST_DEADLINE_SECONDS` | no | Monotonic model-stage deadline; default `9`, maximum `29` |
| `LLM_KEY_COOLDOWN_SECONDS` | no | Cooldown after authentication/rate rejection; default `60` |
| `PORT` | no | HTTP port; default `8000` |
| `LOG_LEVEL` | no | Application log level |

Do not commit `.env` or put credentials in Docker build arguments, image layers, logs, documentation, or API responses.

## Local quickstart

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
cp .env.example .env       # Windows PowerShell: Copy-Item .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Verification

```bash
ruff check .
pytest
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Optimization request:

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  --data @examples/request.json
```

A successful response contains `scenario_id`, one ordered `directive_interpretation` per note, 24 `hourly_plan` entries, `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, and `plan_summary`.

## Docker

```bash
docker build -t gridlock-d:local .
docker run --rm -p 8000:8000 --env-file .env gridlock-d:local
```

The image runs as UID/GID `10001`, binds to `0.0.0.0`, exposes port `8000`, contains a health check, and does not copy `.env`. Before submission, replace the local image name in the submission form with the exact public GHCR tag or digest produced by the release workflow.

## Official public samples

With the service running and the official JSON available:

```bash
python scripts/run_public_samples.py \
  ../BUP_CSE_FEST_2026_Participant_Docs/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

The runner compares directive semantics, independently replays each returned schedule against organizer ground-truth directives, and checks reference cost within `0.01`.

Live provider qualification is separate from unit tests and requires real credentials:

```bash
python scripts/qualify_llm_providers.py --provider all --passes 3
```

It independently exercises Gemini and Groq against all 10 public interpretations plus a synthetic matrix covering percentages, fractions, capacity-relative reserves, noon/midnight, AM/PM, 24-hour time, boundary hours, distractors, multi-note input, and injection-like note data. A configured model is not considered qualified until all three passes succeed. To run the complete real-provider API path, start the service and run the official public-sample command above.

Current qualified configuration (three complete paced passes each):

- Gemini primary: `gemini-3.5-flash-lite` — 60/60, median 1.405 s, max 2.226 s.
- Groq backup: `openai/gpt-oss-120b` — 60/60, median 1.855 s, max 4.812 s.

## CI/CD

- `.github/workflows/ci.yml` runs linting, tests, a Docker build, and a container health smoke test.
- `.github/workflows/release.yml` publishes signed-provenance/SBOM-enabled tagged or manually dispatched images to `ghcr.io/<owner>/<repository>`.

The workflows use the repository `GITHUB_TOKEN`; no provider API key is placed in CI because unit and integration tests inject deterministic fake interpreters. Live-model qualification is run separately with deployment secrets.

## Failure behavior

- `400`: malformed JSON or structurally invalid request
- `422`: reserved for an explicitly defined semantic request rejection
- `500`: controlled model, guardrail, solver, or replay failure

The normal path makes one Gemini call. A Gemini schema/semantic failure permits exactly one Gemini repair and never Groq. A Gemini transport/provider failure permits exactly one Groq call and no repair. Each attempt selects one credential; it never walks the pool after failure. The absolute maximum is two model calls per request, and invalid output never silently becomes `no_op`.

## Known limitations

- A deployment requires a valid configured model, quota, and network availability.
- Groq backup is disabled when no Groq configuration is present and rejected as partial configuration when only a model or only keys are supplied.
- Mocked tests prove orchestration but do not qualify either live model; run the opt-in qualification suite before deployment.
- Conflicting overlapping solar-reduction directives with different factors are rejected because the official specification does not define their composition. Official valid scoring scenarios are stated not to contain contradictory hard directives.

## External tools and libraries

FastAPI, Pydantic, the official Gemini and Groq HTTP APIs with native JSON-schema output, SciPy/HiGHS, HTTPX, pytest, Ruff, Uvicorn, Docker, and GitHub Actions. AI coding assistance was used during implementation; the deterministic scheduling, guardrail, and replay behavior is defined and tested in this repository.

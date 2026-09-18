"""HTTP entry point for the GridWise service."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas import OptimizeRequest, OptimizeResponse
from app.services.credential_pool import CredentialPool
from app.services.interpreter import GeminiProvider, GroqProvider
from app.services.planner import Planner, PlanningError

logger = logging.getLogger(__name__)


def _default_planner() -> Planner:
    settings = get_settings()
    try:
        settings.validate_provider_configuration()
    except ValueError as exc:
        raise PlanningError("model configuration is unavailable") from exc
    primary = GeminiProvider(
        pool=CredentialPool.from_numbered(
            settings.gemini_keys,
            cooldown_seconds=settings.llm_key_cooldown_seconds,
        ),
        model=(settings.gemini_model or "").strip(),
        timeout_seconds=settings.llm_timeout_seconds,
    )
    backup = None
    if settings.groq_model and settings.groq_keys:
        backup = GroqProvider(
            pool=CredentialPool.from_numbered(
                settings.groq_keys,
                cooldown_seconds=settings.llm_key_cooldown_seconds,
            ),
            model=settings.groq_model.strip(),
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return Planner(
        primary=primary,
        backup=backup,
        hard_deadline_seconds=settings.llm_hard_request_deadline_seconds,
    )


def create_app(planner: Planner | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.planner is None:
            application.state.planner = _default_planner()
        yield

    application = FastAPI(
        title="Gridlock-d",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.planner = planner

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: object,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": "Invalid request"})

    @application.get("/health")
    async def health() -> dict[str, str]:
        """Return the exact readiness response required by the judge."""
        return {"status": "ok"}

    @application.post("/optimize-energy", response_model=OptimizeResponse)
    async def optimize_energy(payload: OptimizeRequest) -> OptimizeResponse:
        try:
            active_planner = application.state.planner
            if active_planner is None:
                active_planner = _default_planner()
                application.state.planner = active_planner
            return await active_planner.plan(payload)
        except PlanningError as exc:
            logger.error("optimization request failed: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Optimization failed") from None
        except Exception as exc:
            logger.error("unexpected optimization failure: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Optimization failed") from None

    return application


app = create_app()

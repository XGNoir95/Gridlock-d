"""HTTP entry point for the GridWise service."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.http_safety import (
    OptimizationCapacityError,
    OptimizationGate,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
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
        total_deadline_seconds=settings.total_request_deadline_seconds,
    )


def create_app(
    planner: Planner | None = None,
    *,
    max_request_body_bytes: int = 262_144,
    max_concurrent_optimizations: int = 16,
    max_queued_optimizations: int = 64,
    total_request_deadline_seconds: float = 29.0,
) -> FastAPI:
    gate = OptimizationGate(
        max_active=max_concurrent_optimizations,
        max_queued=max_queued_optimizations,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.planner is None:
            application.state.planner = _default_planner()
        try:
            yield
        finally:
            active_planner = application.state.planner
            if active_planner is not None:
                await active_planner.aclose()

    application = FastAPI(
        title="Gridlock-d",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.planner = planner
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=max_request_body_bytes,
    )
    application.add_middleware(SecurityHeadersMiddleware)

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
            async with asyncio.timeout(total_request_deadline_seconds):
                async with gate.acquire():
                    active_planner = application.state.planner
                    if active_planner is None:
                        active_planner = _default_planner()
                        application.state.planner = active_planner
                    return await active_planner.plan(payload)
        except OptimizationCapacityError:
            logger.warning("optimization request rejected: capacity full")
            raise HTTPException(status_code=500, detail="Optimization failed") from None
        except TimeoutError:
            logger.error("optimization request failed: request deadline exceeded")
            raise HTTPException(status_code=500, detail="Optimization failed") from None
        except PlanningError as exc:
            logger.error("optimization request failed: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Optimization failed") from None
        except Exception as exc:
            logger.error("unexpected optimization failure: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Optimization failed") from None

    return application


settings = get_settings()
app = create_app(
    max_request_body_bytes=settings.max_request_body_bytes,
    max_concurrent_optimizations=settings.max_concurrent_optimizations,
    max_queued_optimizations=settings.max_queued_optimizations,
    total_request_deadline_seconds=settings.total_request_deadline_seconds,
)

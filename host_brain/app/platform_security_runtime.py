"""Post-fork Host ownership for production OBS and Pentest security planes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import FastAPI

from host_brain.observability.command_composition import (
    ObsCommandComposition,
    build_obs_command_composition,
)
from host_brain.observability.secure_command_ingress import (
    SecureObsCommandIngress,
)
from host_brain.pentest.production_runtime import (
    PentestSecureRunComposition,
    build_pentest_secure_run_composition,
)
from host_brain.skills.signed_intake_composition import (
    SignedSkillProviderIntakeComposition,
    build_signed_skill_provider_intake_composition,
)
from host_brain.skills.signed_intake_ingress import (
    SecureSignedSkillProviderIntake,
)


OBS_COMMAND_COMPOSITION_STATE_ATTR = "obs_command_composition"
PENTEST_SECURE_RUN_COMPOSITION_STATE_ATTR = (
    "pentest_secure_run_composition"
)
SKILL_PROVIDER_INTAKE_COMPOSITION_STATE_ATTR = (
    "skill_provider_intake_composition"
)


def get_app_obs_command_ingress(
    app: FastAPI,
) -> SecureObsCommandIngress | None:
    composition = getattr(
        app.state,
        OBS_COMMAND_COMPOSITION_STATE_ATTR,
        None,
    )
    if type(composition) is not ObsCommandComposition or not composition.enabled:
        return None
    ingress = composition.ingress
    return ingress if type(ingress) is SecureObsCommandIngress else None


def get_app_pentest_secure_run_composition(
    app: FastAPI,
) -> PentestSecureRunComposition | None:
    composition = getattr(
        app.state,
        PENTEST_SECURE_RUN_COMPOSITION_STATE_ATTR,
        None,
    )
    return (
        composition
        if type(composition) is PentestSecureRunComposition
        else None
    )


def get_app_signed_skill_provider_intake(
    app: FastAPI,
) -> SecureSignedSkillProviderIntake | None:
    composition = getattr(
        app.state,
        SKILL_PROVIDER_INTAKE_COMPOSITION_STATE_ATTR,
        None,
    )
    if (
        type(composition) is not SignedSkillProviderIntakeComposition
        or not composition.enabled
    ):
        return None
    ingress = composition.ingress
    return (
        ingress
        if type(ingress) is SecureSignedSkillProviderIntake
        else None
    )


@asynccontextmanager
async def platform_security_runtime_lifespan(
    application: FastAPI,
    *,
    get_ledger: Callable[[], Any],
    get_token_service: Callable[[], Any],
    logger: Any,
    obs_builder: Callable[[], Any] = build_obs_command_composition,
    pentest_builder: Callable[..., Any] = (
        build_pentest_secure_run_composition
    ),
    skill_intake_builder: Callable[..., Any] = (
        build_signed_skill_provider_intake_composition
    ),
) -> AsyncGenerator[None, None]:
    """Construct after the worker starts and release before base shutdown."""

    obs_composition: Any | None = None
    pentest_composition: Any | None = None
    skill_intake_composition: Any | None = None
    try:
        try:
            ledger = get_ledger()
            canonical_db_path = str(
                getattr(ledger, "canonical_db_path", "") or ""
            ).strip()
            if not canonical_db_path:
                raise RuntimeError("HOST_CANONICAL_AUDIT_DB_UNAVAILABLE")
        except Exception:
            logger.error("HOST_CANONICAL_AUDIT_DB_UNAVAILABLE")
            canonical_db_path = ""

        try:
            obs_composition = (
                obs_builder(db_path=canonical_db_path)
                if canonical_db_path
                else None
            )
        except Exception:
            logger.error("OBS_COMMAND_PRODUCTION_CONSTRUCTION_FAILED")
            obs_composition = None
        setattr(
            application.state,
            OBS_COMMAND_COMPOSITION_STATE_ATTR,
            obs_composition,
        )

        try:
            skill_intake_composition = (
                skill_intake_builder(db_path=canonical_db_path)
                if canonical_db_path
                else None
            )
        except Exception:
            logger.error("SKILL_PROVIDER_INTAKE_PRODUCTION_CONSTRUCTION_FAILED")
            skill_intake_composition = None
        setattr(
            application.state,
            SKILL_PROVIDER_INTAKE_COMPOSITION_STATE_ATTR,
            skill_intake_composition,
        )

        try:
            token_service = get_token_service()
            pentest_composition = (
                pentest_builder(
                    token_service=token_service,
                    db_path=canonical_db_path,
                )
                if canonical_db_path
                else None
            )
        except Exception:
            logger.error("PENTEST_PRODUCTION_CONSTRUCTION_FAILED")
            pentest_composition = None
        setattr(
            application.state,
            PENTEST_SECURE_RUN_COMPOSITION_STATE_ATTR,
            pentest_composition,
        )
        yield
    finally:
        # Stop new OBS admission before hiding the provider.  Existing admitted
        # work retains the object and is drained by ObsCommandComposition.
        if obs_composition is not None:
            ingress = getattr(obs_composition, "ingress", None)
            close_admission = getattr(ingress, "close_admission", None)
            if callable(close_admission):
                try:
                    close_admission()
                except Exception:
                    logger.error("OBS_COMMAND_ADMISSION_CLOSE_FAILED")

        setattr(
            application.state,
            OBS_COMMAND_COMPOSITION_STATE_ATTR,
            None,
        )
        setattr(
            application.state,
            PENTEST_SECURE_RUN_COMPOSITION_STATE_ATTR,
            None,
        )
        setattr(
            application.state,
            SKILL_PROVIDER_INTAKE_COMPOSITION_STATE_ATTR,
            None,
        )

        if obs_composition is not None:
            aclose = getattr(obs_composition, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    logger.error("OBS_COMMAND_DRAIN_CLOSE_FAILED")
        if pentest_composition is not None:
            close = getattr(pentest_composition, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.error("PENTEST_PRODUCTION_CLOSE_FAILED")
        if skill_intake_composition is not None:
            close = getattr(skill_intake_composition, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.error("SKILL_PROVIDER_INTAKE_PRODUCTION_CLOSE_FAILED")


__all__ = [
    "OBS_COMMAND_COMPOSITION_STATE_ATTR",
    "PENTEST_SECURE_RUN_COMPOSITION_STATE_ATTR",
    "SKILL_PROVIDER_INTAKE_COMPOSITION_STATE_ATTR",
    "get_app_obs_command_ingress",
    "get_app_pentest_secure_run_composition",
    "get_app_signed_skill_provider_intake",
    "platform_security_runtime_lifespan",
]

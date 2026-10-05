"""Admin-only Heym Work integration settings."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_instance_admin
from app.db.models import User
from app.db.session import get_db
from app.models.work_integration_schemas import (
    WorkIntegrationConfigResponse,
    WorkIntegrationConfigUpdate,
    WorkKeyResponse,
)
from app.services.audit_log import audit
from app.services.secret_tokens import hash_secret
from app.services.work_integration import (
    WorkUrlError,
    clear_key_cache,
    get_work_integration,
    mint_work_key,
    normalize_work_url,
)

router = APIRouter()


@router.get("/config", response_model=WorkIntegrationConfigResponse)
async def get_work_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkIntegrationConfigResponse:
    require_instance_admin(current_user)
    row = await get_work_integration(db)
    return WorkIntegrationConfigResponse.from_row(row)


@router.put("/config", response_model=WorkIntegrationConfigResponse)
async def update_work_config(
    update: WorkIntegrationConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkIntegrationConfigResponse:
    require_instance_admin(current_user)
    row = await get_work_integration(db)

    if update.work_url is not None:
        try:
            row.work_url = normalize_work_url(update.work_url)
        except WorkUrlError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if update.enabled is not None:
        if update.enabled and not (row.work_url and row.key_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Set the Heym Work address and generate a key before enabling it.",
            )
        row.enabled = update.enabled

    row.updated_by_id = current_user.id
    await db.flush()
    await db.refresh(row)
    clear_key_cache()

    audit(
        action="work_integration.update",
        actor=current_user,
        target_type="work_integration",
        enabled=row.enabled,
        work_url=row.work_url,
    )
    return WorkIntegrationConfigResponse.from_row(row)


@router.post("/key", response_model=WorkKeyResponse)
async def rotate_work_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkKeyResponse:
    require_instance_admin(current_user)
    row = await get_work_integration(db)

    key = mint_work_key()
    row.key_hash = hash_secret(key)
    row.key_rotated_at = datetime.now(timezone.utc)
    row.updated_by_id = current_user.id
    await db.flush()
    await db.refresh(row)
    clear_key_cache()

    audit(action="work_integration.key_rotate", actor=current_user, target_type="work_integration")
    return WorkKeyResponse(key=key, config=WorkIntegrationConfigResponse.from_row(row))

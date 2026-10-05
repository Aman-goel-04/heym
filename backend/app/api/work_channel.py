"""The Heym Work channel. Every endpoint requires the integration key."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import password_sign_in, register_account
from app.api.deps import get_client_ip
from app.config import settings
from app.db.models import User, WorkIntegration
from app.db.session import get_db
from app.models.schemas import UserCreate, UserLogin
from app.models.work_integration_schemas import (
    WorkAuditRequest,
    WorkHandshakeRequest,
    WorkHandshakeResponse,
    WorkRefreshRequest,
    WorkSsoExchangeRequest,
    WorkTokenResponse,
    WorkUserSummary,
)
from app.services.audit_log import OUTCOME_DENIED, audit
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    read_refresh_claims,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.services.sso_settings import get_sso_settings
from app.services.work_integration import (
    WORK_CLIENT,
    WORK_KEY_HEADER,
    consume_sso_code,
    get_work_integration,
    issue_work_tokens,
    key_matches,
    key_version,
    work_client_ip,
)

router = APIRouter()


async def require_work_channel(
    request: Request, db: AsyncSession = Depends(get_db)
) -> WorkIntegration:
    """Admit only requests that carry the current key of an enabled integration."""
    row = await get_work_integration(db)
    if not (row.enabled and key_matches(row.key_hash, request.headers.get(WORK_KEY_HEADER))):
        audit(action="work.channel_auth", outcome=OUTCOME_DENIED, reason="bad_key_or_disabled")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Heym Work key"
        )
    return row


def _summary(user: User) -> WorkUserSummary:
    return WorkUserSummary(id=str(user.id), email=user.email, name=user.name)


async def _load_user(db: AsyncSession, user_id: object) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


@router.post("/handshake", response_model=WorkHandshakeResponse)
async def handshake(
    body: WorkHandshakeRequest,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> WorkHandshakeResponse:
    now = datetime.now(timezone.utc)
    row.handshake_nonce = body.nonce
    row.handshake_at = now
    row.last_seen_at = now
    row.last_work_version = body.work_version
    row.last_license_status = body.license_status

    sso = await get_sso_settings(db)
    sso_enabled = bool(sso.enabled and sso.issuer and sso.client_id)
    password_login = "admins_only" if sso_enabled and sso.password_login_disabled else "allowed"

    # Work reads the nonce back from the database right after this response.
    await db.commit()
    return WorkHandshakeResponse(
        heym_version=settings.resolved_version,
        sso_enabled=sso_enabled,
        sso_button_label=sso.button_label,
        password_login=password_login,
        registration_allowed=bool(settings.allow_register and password_login == "allowed"),
        work_url=row.work_url,
    )


@router.post("/login", response_model=WorkTokenResponse)
async def channel_login(
    body: UserLogin,
    request: Request,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> WorkTokenResponse:
    ip = work_client_ip(request, fallback=get_client_ip(request))
    user = await password_sign_in(db, body, ip, client=WORK_CLIENT)
    tokens = await issue_work_tokens(db, user.id, row)
    audit(action="auth.login", actor=user, client=WORK_CLIENT)
    return WorkTokenResponse(
        access_token=tokens.access_token, refresh_token=tokens.refresh_token, user=_summary(user)
    )


@router.post("/register", response_model=WorkTokenResponse, status_code=status.HTTP_201_CREATED)
async def channel_register(
    body: UserCreate,
    request: Request,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> WorkTokenResponse:
    ip = work_client_ip(request, fallback=get_client_ip(request))
    user = await register_account(db, body, ip, client=WORK_CLIENT)
    tokens = await issue_work_tokens(db, user.id, row)
    audit(action="auth.register", actor=user, client=WORK_CLIENT)
    return WorkTokenResponse(
        access_token=tokens.access_token, refresh_token=tokens.refresh_token, user=_summary(user)
    )


@router.post("/sso/exchange", response_model=WorkTokenResponse)
async def channel_sso_exchange(
    body: WorkSsoExchangeRequest,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> WorkTokenResponse:
    user_id = await consume_sso_code(db, body.code)
    user = await _load_user(db, user_id) if user_id is not None else None
    if user is None:
        audit(
            action="auth.sso_exchange",
            outcome=OUTCOME_DENIED,
            client=WORK_CLIENT,
            reason="invalid_code",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign-in code is invalid or expired",
        )
    tokens = await issue_work_tokens(db, user.id, row)
    audit(action="auth.sso_exchange", actor=user, client=WORK_CLIENT)
    return WorkTokenResponse(
        access_token=tokens.access_token, refresh_token=tokens.refresh_token, user=_summary(user)
    )


@router.post("/refresh", response_model=WorkTokenResponse)
async def channel_refresh(
    body: WorkRefreshRequest,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> WorkTokenResponse:
    current = key_version(row.key_hash or "")
    claims = read_refresh_claims(body.refresh_token)
    if claims is None or claims.client != WORK_CLIENT or claims.key_version != current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user = await _load_user(db, claims.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    new_access = create_access_token(user.id, client=WORK_CLIENT, key_version=current)
    new_refresh = create_refresh_token(user.id, client=WORK_CLIENT, key_version=current)
    if not await rotate_refresh_token(db, body.refresh_token, new_refresh, user.id):
        audit(
            action="auth.token_refresh",
            outcome=OUTCOME_DENIED,
            actor=user,
            client=WORK_CLIENT,
            reason="refresh_token_replayed_or_revoked",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used or revoked",
        )
    audit(action="auth.token_refresh", actor=user, client=WORK_CLIENT)
    return WorkTokenResponse(
        access_token=new_access, refresh_token=new_refresh, user=_summary(user)
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def channel_logout(
    body: WorkRefreshRequest,
    row: WorkIntegration = Depends(require_work_channel),
    db: AsyncSession = Depends(get_db),
) -> None:
    claims = read_refresh_claims(body.refresh_token)
    if claims is not None and claims.client == WORK_CLIENT:
        await revoke_refresh_token(db, body.refresh_token)
        audit(action="auth.logout", actor_id=claims.user_id, client=WORK_CLIENT)


@router.post("/audit", status_code=status.HTTP_204_NO_CONTENT)
async def channel_audit(
    body: WorkAuditRequest,
    row: WorkIntegration = Depends(require_work_channel),
) -> None:
    audit(
        action=body.action,
        outcome=body.outcome,
        actor_id=body.actor_id,
        target_type=body.target_type,
        target_id=body.target_id,
        target_name=body.target_name,
        source="work",
        **body.detail,
    )

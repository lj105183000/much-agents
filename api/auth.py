"""Local accounts, revocable sessions, and role-based API guards."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from .database import UserRow, UserSessionRow

logger = logging.getLogger(__name__)

Role = Literal["admin", "bid_manager", "writer", "reviewer", "viewer"]
ROLES: tuple[str, ...] = ("admin", "bid_manager", "writer", "reviewer", "viewer")
SERVICE_ROLE = "service"
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
ADMIN_UPDATE_LOCK_ID = 0x4D554155  # "MUAU": serializes last-admin checks
SESSION_PURGE_DAYS = 7
_WEAK_PASSWORDS = frozenset(
    {
        "123456789012",
        "password1234",
        "password123456",
        "qwertyuiop12",
        "admin1234567",
        "administrator",
        "letmein123456",
        "changeme12345",
    }
)

_hasher = PasswordHasher()
_dummy_hash: str | None = None


class AuthError(Exception):
    """An authentication or authorization error surfaced as an HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    username: str
    display_name: str
    password_hash: str
    role: str
    is_active: bool
    failed_login_count: int
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    user_id: str
    expires_at: datetime
    revoked_at: datetime | None
    username: str
    display_name: str
    role: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class UserContext:
    id: str
    username: str
    display_name: str
    role: str
    session_id: str | None = None
    is_service: bool = False


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    session_ttl_hours: int = 12
    max_failures: int = 5
    lockout_minutes: int = 15


class LoginAttemptLimiter:
    """In-process per-IP failed-login window.

    This complements per-account lockout and Nginx's ``limit_req``. It is not
    shared between API replicas; production runs a single API process.
    """

    def __init__(self, *, max_failures: int = 20, window_seconds: int = 300) -> None:
        self.max_failures = max(1, max_failures)
        self.window_seconds = max(1, window_seconds)
        self._entries: dict[str, deque[float]] = {}
        self._last_prune = 0.0

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._entries.get(key)
        if bucket is None:
            bucket = deque()
            self._entries[key] = bucket
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def _prune_stale(self, now: float) -> None:
        if now - self._last_prune < self.window_seconds:
            return
        self._last_prune = now
        cutoff = now - self.window_seconds
        for key, bucket in list(self._entries.items()):
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                self._entries.pop(key, None)

    def allow(self, ip: str | None) -> bool:
        if not ip:
            return True
        now = time.monotonic()
        self._prune_stale(now)
        return len(self._prune(ip, now)) < self.max_failures

    def record_failure(self, ip: str | None) -> None:
        if not ip:
            return
        now = time.monotonic()
        self._prune_stale(now)
        self._prune(ip, now).append(now)

    def reset(self, ip: str | None) -> None:
        if ip:
            self._entries.pop(ip, None)


def client_ip(request: Request, trusted_proxies: tuple[str, ...] = ()) -> str | None:
    """Return the client address, honoring X-Forwarded-For only from proxies."""
    peer = request.client.host if request.client else None
    if peer and ("*" in trusted_proxies or peer in trusted_proxies):
        forwarded = request.headers.get("x-forwarded-for", "")
        candidate = forwarded.split(",", 1)[0].strip()
        if candidate and len(candidate) <= 64:
            return candidate
    return peer[:64] if peer else None


LOCAL_USER = UserContext(
    id="local",
    username="local",
    display_name="本地模式",
    role="admin",
)
SERVICE_USER = UserContext(
    id="mcp-service",
    username="mcp-service",
    display_name="MCP 服务账号",
    role=SERVICE_ROLE,
    is_service=True,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.fullmatch(normalized):
        raise AuthError(
            400,
            "用户名需为 3–64 位小写字母、数字、点、下划线或连字符，且以字母或数字开头",
        )
    return normalized


def validate_password(password: str, *, username: str = "") -> str:
    if not isinstance(password, str):
        raise AuthError(400, "密码格式不正确")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(400, f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(400, f"密码不能超过 {MAX_PASSWORD_LENGTH} 个字符")
    if username and password.strip().lower() == normalize_username(username):
        raise AuthError(400, "密码不能与用户名相同")
    if password.strip().lower() in _WEAK_PASSWORDS:
        raise AuthError(400, "密码过于常见，请更换更强的密码")
    return password


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def _verify_password_sync(password_hash: str, password: str) -> bool:
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, password_hash, password)


def _dummy_hash_value() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hash_password_sync(secrets.token_urlsafe(24))
    return _dummy_hash


def _user_record(row: UserRow) -> UserRecord:
    now = _now()
    return UserRecord(
        id=row.id,
        username=row.username,
        display_name=row.display_name,
        password_hash=row.password_hash,
        role=row.role,
        is_active=bool(row.is_active),
        failed_login_count=int(row.failed_login_count or 0),
        locked_until=_aware(row.locked_until),
        last_login_at=_aware(row.last_login_at),
        created_at=_aware(row.created_at) or now,
        updated_at=_aware(row.updated_at) or now,
    )


class AuthRepository:
    """Persistence for local users and sessions."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        statement = select(UserRow).where(UserRow.username == username)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _user_record(row) if row is not None else None

    async def get_user(self, user_id: str) -> UserRecord | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
            return _user_record(row) if row is not None else None

    async def list_users(self, limit: int = 200) -> list[UserRecord]:
        statement = select(UserRow).order_by(UserRow.username).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_user_record(row) for row in rows]

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
    ) -> UserRecord:
        now = _now()
        row = UserRow(
            id=uuid4().hex,
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
            is_active=True,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(row)
                    await session.flush()
        except IntegrityError as exc:
            raise AuthError(409, "用户名已存在") from exc
        return _user_record(row)

    async def update_user(
        self,
        user_id: str,
        *,
        enforce_last_admin: bool = False,
        **fields: object,
    ) -> UserRecord | None:
        allowed = {"display_name", "role", "is_active"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return await self.get_user(user_id)
        values["updated_at"] = _now()
        statement = update(UserRow).where(UserRow.id == user_id).values(**values)
        async with self._session_factory() as session:
            async with session.begin():
                if enforce_last_admin:
                    # Lock admin mutations and re-check inside the same
                    # transaction so two concurrent demotions cannot both
                    # observe two active admins.
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": ADMIN_UPDATE_LOCK_ID},
                    )
                    current = await session.get(
                        UserRow, user_id, with_for_update=True
                    )
                    if current is None:
                        return None
                    removes_admin = (
                        current.role == "admin"
                        and bool(current.is_active)
                        and (
                            values.get("role", current.role) != "admin"
                            or values.get("is_active", True) is False
                        )
                    )
                    if removes_admin:
                        count = int(
                            (
                                await session.execute(
                                    select(func.count(UserRow.id)).where(
                                        UserRow.role == "admin",
                                        UserRow.is_active.is_(True),
                                    )
                                )
                            ).scalar_one()
                            or 0
                        )
                        if count <= 1:
                            raise AuthError(409, "不能停用或降级最后一个管理员")
                result = await session.execute(statement)
                if result.rowcount == 0:
                    return None
                row = await session.get(UserRow, user_id)
                return _user_record(row) if row is not None else None

    async def update_password(self, user_id: str, password_hash: str) -> bool:
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(
                password_hash=password_hash,
                failed_login_count=0,
                locked_until=None,
                updated_at=_now(),
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    async def record_login_failure(
        self,
        user_id: str,
        *,
        max_failures: int,
        lockout_minutes: int,
    ) -> UserRecord | None:
        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(UserRow, user_id, with_for_update=True)
                if row is None:
                    return None
                row.failed_login_count = int(row.failed_login_count or 0) + 1
                if row.failed_login_count >= max_failures:
                    row.locked_until = now + timedelta(minutes=lockout_minutes)
                row.updated_at = now
                await session.flush()
                return _user_record(row)

    async def reset_login_failure(self, user_id: str) -> None:
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(
                failed_login_count=0,
                locked_until=None,
                last_login_at=_now(),
                updated_at=_now(),
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(statement)

    async def count_active_admins(self) -> int:
        statement = select(func.count(UserRow.id)).where(
            UserRow.role == "admin",
            UserRow.is_active.is_(True),
        )
        async with self._session_factory() as session:
            return int((await session.execute(statement)).scalar_one() or 0)

    async def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> SessionRecord:
        now = _now()
        row = UserSessionRow(
            id=uuid4().hex,
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
        return SessionRecord(
            id=row.id,
            user_id=row.user_id,
            expires_at=expires_at,
            revoked_at=None,
            username="",
            display_name="",
            role="",
            is_active=True,
        )

    async def get_session_by_token_hash(
        self, token_hash: str
    ) -> SessionRecord | None:
        now = _now()
        statement = (
            select(UserSessionRow, UserRow)
            .join(UserRow, UserRow.id == UserSessionRow.user_id)
            .where(
                UserSessionRow.token_hash == token_hash,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
                UserRow.is_active.is_(True),
            )
        )
        async with self._session_factory() as session:
            result = (await session.execute(statement)).first()
        if result is None:
            return None
        session_row, user_row = result
        return SessionRecord(
            id=session_row.id,
            user_id=session_row.user_id,
            expires_at=_aware(session_row.expires_at) or now,
            revoked_at=_aware(session_row.revoked_at),
            username=user_row.username,
            display_name=user_row.display_name,
            role=user_row.role,
            is_active=bool(user_row.is_active),
        )

    async def revoke_session(self, session_id: str) -> bool:
        statement = (
            update(UserSessionRow)
            .where(
                UserSessionRow.id == session_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    async def revoke_user_sessions(self, user_id: str) -> int:
        statement = (
            update(UserSessionRow)
            .where(
                UserSessionRow.user_id == user_id,
                UserSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return int(result.rowcount or 0)

    async def purge_sessions(self, *, older_than_days: int = SESSION_PURGE_DAYS) -> int:
        """Delete expired or long-revoked sessions.

        Only rows older than the retention window are removed so recent
        authentication failures remain debuggable.
        """
        cutoff = _now() - timedelta(days=max(1, older_than_days))
        statement = delete(UserSessionRow).where(
            or_(
                UserSessionRow.expires_at < cutoff,
                UserSessionRow.revoked_at < cutoff,
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return int(result.rowcount or 0)


def _user_context(user: UserRecord, session_id: str | None = None) -> UserContext:
    return UserContext(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        session_id=session_id,
    )


class AuthService:
    """Business rules for local accounts and sessions."""

    def __init__(
        self,
        repo: AuthRepository,
        policy: AuthPolicy | None = None,
        *,
        hash_concurrency: int = 2,
    ) -> None:
        self.repo = repo
        self.policy = policy or AuthPolicy()
        self._hash_semaphore = asyncio.Semaphore(max(1, hash_concurrency))

    async def _hash_password(self, password: str) -> str:
        async with self._hash_semaphore:
            return await hash_password(password)

    async def _verify_password(self, password_hash: str, password: str) -> bool:
        async with self._hash_semaphore:
            return await verify_password(password_hash, password)

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str | None = None,
        role: str = "viewer",
    ) -> UserRecord:
        normalized = validate_username(username)
        if role not in ROLES:
            raise AuthError(400, "无效的角色")
        validate_password(password, username=normalized)
        name = (display_name or "").strip() or normalized
        if len(name) > 128:
            raise AuthError(400, "显示名称过长")
        password_digest = await self._hash_password(password)
        return await self.repo.create_user(
            username=normalized,
            display_name=name,
            password_hash=password_digest,
            role=role,
        )

    async def list_users(self) -> list[UserRecord]:
        return await self.repo.list_users()

    async def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> UserRecord:
        current = await self.repo.get_user(user_id)
        if current is None:
            raise AuthError(404, "用户不存在")
        fields: dict[str, object] = {}
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise AuthError(400, "显示名称不能为空")
            if len(name) > 128:
                raise AuthError(400, "显示名称过长")
            fields["display_name"] = name
        if role is not None:
            if role not in ROLES:
                raise AuthError(400, "无效的角色")
            fields["role"] = role
        if is_active is not None:
            fields["is_active"] = bool(is_active)
        role_or_state_change = role is not None or is_active is not None
        updated = await self.repo.update_user(
            user_id,
            enforce_last_admin=role_or_state_change,
            **fields,
        )
        if updated is None:
            raise AuthError(404, "用户不存在")
        if is_active is False or (role is not None and role != current.role):
            await self.repo.revoke_user_sessions(user_id)
        return updated

    async def reset_password(self, user_id: str, new_password: str) -> None:
        user = await self.repo.get_user(user_id)
        if user is None:
            raise AuthError(404, "用户不存在")
        validate_password(new_password, username=user.username)
        digest = await self._hash_password(new_password)
        if not await self.repo.update_password(user_id, digest):
            raise AuthError(404, "用户不存在")
        await self.repo.revoke_user_sessions(user_id)

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        user = await self.repo.get_user(user_id)
        if user is None or not user.is_active:
            raise AuthError(401, "账号不可用")
        if not await self._verify_password(
            user.password_hash, current_password
        ):
            raise AuthError(400, "当前密码不正确")
        validate_password(new_password, username=user.username)
        digest = await self._hash_password(new_password)
        if not await self.repo.update_password(user_id, digest):
            raise AuthError(404, "用户不存在")
        await self.repo.revoke_user_sessions(user_id)

    async def authenticate(self, username: str, password: str) -> UserRecord:
        normalized = normalize_username(username)
        if not normalized or not password:
            raise AuthError(401, "用户名或密码错误")
        user = await self.repo.get_user_by_username(normalized)
        if user is None:
            # Verify against a dummy hash so unknown usernames take about as
            # long as wrong passwords.
            await self._verify_password(_dummy_hash_value(), password)
            raise AuthError(401, "用户名或密码错误")
        locked_until = _aware(user.locked_until)
        now = _now()
        if locked_until is not None and locked_until > now:
            remaining = max(1, int((locked_until - now).total_seconds()))
            raise AuthError(429, f"账号已锁定，请在 {remaining} 秒后重试")
        if not user.is_active:
            raise AuthError(401, "用户名或密码错误")
        if not await self._verify_password(user.password_hash, password):
            await self.repo.record_login_failure(
                user.id,
                max_failures=self.policy.max_failures,
                lockout_minutes=self.policy.lockout_minutes,
            )
            raise AuthError(401, "用户名或密码错误")
        await self.repo.reset_login_failure(user.id)
        refreshed = await self.repo.get_user(user.id)
        return refreshed or user

    async def create_session(
        self,
        user: UserRecord,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, datetime]:
        token = new_session_token()
        expires_at = _now() + timedelta(hours=self.policy.session_ttl_hours)
        await self.repo.create_session(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=expires_at,
            ip=(ip or "")[:64] or None,
            user_agent=(user_agent or "")[:255] or None,
        )
        try:
            await self.repo.purge_sessions(older_than_days=SESSION_PURGE_DAYS)
        except Exception:  # noqa: BLE001 - cleanup must not block login
            logger.warning("could not purge expired sessions", exc_info=True)
        return token, expires_at

    async def resolve_session(self, token: str) -> UserContext | None:
        if not token:
            return None
        record = await self.repo.get_session_by_token_hash(hash_session_token(token))
        if record is None or not record.is_active:
            return None
        return UserContext(
            id=record.user_id,
            username=record.username,
            display_name=record.display_name,
            role=record.role,
            session_id=record.id,
        )

    async def revoke_session(self, session_id: str) -> None:
        await self.repo.revoke_session(session_id)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "").strip()
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _unauthorized(detail: str = "authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_auth_service(request: Request) -> AuthService | None:
    return getattr(request.app.state, "auth_service", None)


async def require_user(request: Request) -> UserContext:
    settings = getattr(request.app.state, "api_settings", None)
    auth_enabled = bool(getattr(settings, "auth_enabled", False))
    auth_service = get_auth_service(request)
    token = _bearer_token(request)
    expected = getattr(settings, "mcp_api_token", "") if settings is not None else ""

    if token and expected and hmac.compare_digest(token, expected):
        request.state.user = SERVICE_USER
        return SERVICE_USER

    if not auth_enabled:
        request.state.user = LOCAL_USER
        return LOCAL_USER

    if token is None:
        raise _unauthorized()
    if auth_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is unavailable",
        )
    user = await auth_service.resolve_session(token)
    if user is None:
        raise _unauthorized("invalid or expired session")
    request.state.user = user
    return user


def require_role(
    *roles: str,
    allow_service: bool = True,
) -> Callable[..., object]:
    allowed = frozenset(roles)

    async def dependency(user: UserContext = Depends(require_user)) -> UserContext:
        if user.is_service:
            if allow_service:
                return user
            raise HTTPException(status_code=403, detail="permission denied")
        if user.role == "admin" or user.role in allowed:
            return user
        raise HTTPException(status_code=403, detail="permission denied")

    return dependency


require_write = require_role("bid_manager", "writer")
require_review = require_role("bid_manager", "writer", "reviewer")
require_manage = require_role("bid_manager")


async def require_admin(user: UserContext = Depends(require_user)) -> UserContext:
    if user.is_service or user.role != "admin":
        raise HTTPException(status_code=403, detail="permission denied")
    return user


def can_manage(user: UserContext) -> bool:
    """Bid managers and admins may manage shared company data and exports."""
    return user.role in {"admin", "bid_manager"}


__all__ = [
    "AuthError",
    "AuthPolicy",
    "AuthRepository",
    "AuthService",
    "LOCAL_USER",
    "LoginAttemptLimiter",
    "MIN_PASSWORD_LENGTH",
    "ROLES",
    "SERVICE_ROLE",
    "SERVICE_USER",
    "SessionRecord",
    "UserContext",
    "UserRecord",
    "can_manage",
    "client_ip",
    "get_auth_service",
    "hash_password",
    "hash_session_token",
    "new_session_token",
    "normalize_username",
    "require_admin",
    "require_manage",
    "require_review",
    "require_role",
    "require_user",
    "require_write",
    "validate_password",
    "validate_username",
    "verify_password",
]

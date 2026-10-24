from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from api.auth import (
    AuthError,
    AuthPolicy,
    AuthService,
    hash_session_token,
    validate_password,
    verify_password,
)
from tests.fakes import FakeAuthRepository

PASSWORD = "correct horse battery"


def make_service(**policy: int) -> tuple[AuthService, FakeAuthRepository]:
    repo = FakeAuthRepository()
    service = AuthService(
        repo,  # type: ignore[arg-type]
        AuthPolicy(
            session_ttl_hours=policy.get("session_ttl_hours", 1),
            max_failures=policy.get("max_failures", 2),
            lockout_minutes=policy.get("lockout_minutes", 5),
        ),
    )
    return service, repo


def test_create_user_normalizes_and_hashes_password() -> None:
    async def scenario() -> None:
        service, repo = make_service()
        user = await service.create_user(
            "  Alice ",
            PASSWORD,
            display_name="爱丽丝",
            role="writer",
        )
        assert user.username == "alice"
        assert user.display_name == "爱丽丝"
        assert user.role == "writer"
        assert user.password_hash != PASSWORD
        assert await verify_password(user.password_hash, PASSWORD)
        assert await repo.get_user_by_username("alice") is not None

        with pytest.raises(AuthError) as duplicate:
            await service.create_user("alice", PASSWORD)
        assert duplicate.value.status_code == 409

    asyncio.run(scenario())


def test_rejects_invalid_username_role_and_weak_password() -> None:
    async def scenario() -> None:
        service, _repo = make_service()
        with pytest.raises(AuthError):
            await service.create_user("ab", PASSWORD)
        with pytest.raises(AuthError):
            await service.create_user("张三", PASSWORD)
        with pytest.raises(AuthError):
            await service.create_user("valid-name", PASSWORD, role="service")
        with pytest.raises(AuthError):
            await service.create_user("valid-name", "short")
        with pytest.raises(AuthError):
            await service.create_user("weakpass", "password123456")
        with pytest.raises(AuthError):
            await service.create_user("samepassword", "samepassword")

    asyncio.run(scenario())


def test_validate_password_accepts_long_unique_passwords() -> None:
    assert validate_password(PASSWORD) == PASSWORD


def test_authenticate_success_and_failure_tracking() -> None:
    async def scenario() -> None:
        service, repo = make_service()
        user = await service.create_user("alice", PASSWORD)

        with pytest.raises(AuthError) as wrong:
            await service.authenticate("ALICE", "wrong password value")
        assert wrong.value.status_code == 401
        stored = await repo.get_user(user.id)
        assert stored is not None and stored.failed_login_count == 1

        authenticated = await service.authenticate("alice", PASSWORD)
        assert authenticated.id == user.id
        stored = await repo.get_user(user.id)
        assert stored is not None
        assert stored.failed_login_count == 0
        assert stored.last_login_at is not None

    asyncio.run(scenario())


def test_authenticate_locks_after_max_failures() -> None:
    async def scenario() -> None:
        service, _repo = make_service(max_failures=2, lockout_minutes=5)
        await service.create_user("alice", PASSWORD)
        for _ in range(2):
            with pytest.raises(AuthError) as failure:
                await service.authenticate("alice", "wrong password value")
            assert failure.value.status_code == 401
        with pytest.raises(AuthError) as locked:
            await service.authenticate("alice", PASSWORD)
        assert locked.value.status_code == 429

    asyncio.run(scenario())


def test_unknown_user_is_rejected() -> None:
    async def scenario() -> None:
        service, _repo = make_service()
        with pytest.raises(AuthError) as failure:
            await service.authenticate("nobody", PASSWORD)
        assert failure.value.status_code == 401

    asyncio.run(scenario())


def test_session_lifecycle_and_revocation() -> None:
    async def scenario() -> None:
        service, repo = make_service()
        user = await service.create_user("alice", PASSWORD)
        token, expires_at = await service.create_session(
            user,
            ip="127.0.0.1",
            user_agent="pytest",
        )
        assert expires_at > datetime.now(timezone.utc)
        context = await service.resolve_session(token)
        assert context is not None
        assert context.id == user.id
        assert context.role == "viewer"
        assert context.session_id is not None

        assert await service.revoke_session(context.session_id) is None
        assert await service.resolve_session(token) is None

        # An expired session must not resolve even if not revoked.
        token2, _expires = await service.create_session(user)
        record = repo.sessions[hash_session_token(token2)]
        repo.sessions[hash_session_token(token2)] = replace(
            record,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        assert await service.resolve_session(token2) is None

    asyncio.run(scenario())


def test_deactivating_user_revokes_sessions() -> None:
    async def scenario() -> None:
        service, repo = make_service()
        admin = await service.create_user("admin", PASSWORD, role="admin")
        viewer = await service.create_user("viewer", PASSWORD, role="viewer")
        token, _ = await service.create_session(viewer)
        assert await service.resolve_session(token) is not None

        await service.update_user(viewer.id, is_active=False)
        assert await service.resolve_session(token) is None
        stored = await repo.get_user(viewer.id)
        assert stored is not None and stored.is_active is False

        # Recreating the session directly still fails because the user is inactive.
        fresh = await service.create_session(stored)
        assert await service.resolve_session(fresh[0]) is None
        assert admin.role == "admin"

    asyncio.run(scenario())


def test_last_admin_cannot_be_demoted_or_disabled() -> None:
    async def scenario() -> None:
        service, _repo = make_service()
        admin = await service.create_user("admin", PASSWORD, role="admin")
        with pytest.raises(AuthError) as demote:
            await service.update_user(admin.id, role="viewer")
        assert demote.value.status_code == 409
        with pytest.raises(AuthError) as disable:
            await service.update_user(admin.id, is_active=False)
        assert disable.value.status_code == 409

        await service.create_user("admin2", PASSWORD, role="admin")
        updated = await service.update_user(admin.id, role="viewer")
        assert updated.role == "viewer"

    asyncio.run(scenario())


def test_password_change_and_reset_revoke_sessions() -> None:
    async def scenario() -> None:
        service, _repo = make_service()
        user = await service.create_user("alice", PASSWORD)
        token, _ = await service.create_session(user)

        with pytest.raises(AuthError) as wrong:
            await service.change_password(user.id, "wrong password value", "new password value")
        assert wrong.value.status_code == 400

        await service.change_password(user.id, PASSWORD, "new password value")
        assert await service.resolve_session(token) is None
        with pytest.raises(AuthError):
            await service.authenticate("alice", PASSWORD)
        refreshed = await service.authenticate("alice", "new password value")
        assert refreshed.id == user.id

        token2, _ = await service.create_session(refreshed)
        await service.reset_password(user.id, "third password value")
        assert await service.resolve_session(token2) is None
        await service.authenticate("alice", "third password value")

    asyncio.run(scenario())

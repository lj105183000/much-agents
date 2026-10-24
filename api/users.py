"""Create and maintain local Much Agents accounts.

Examples:
    uv run python -m api.users create-admin --username admin
    uv run python -m api.users create-user --username zhangsan --role writer
    uv run python -m api.users reset-password --username admin
    uv run python -m api.users list
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from .auth import (
    ROLES,
    AuthError,
    AuthPolicy,
    AuthRepository,
    AuthService,
    normalize_username,
)
from .config import ApiSettings
from .db import Database
from .projects import ProjectRepository


def _read_password(*, password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise SystemExit("密码不能为空")
        return password
    first = getpass.getpass("密码: ")
    second = getpass.getpass("确认密码: ")
    if first != second:
        raise SystemExit("两次输入的密码不一致")
    return first


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.users",
        description="管理 Much Agents 本地账号（需要 PostgreSQL 与已应用的迁移）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="创建普通用户")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", default=None)
    create.add_argument(
        "--role",
        default="viewer",
        choices=[role for role in ROLES if role != "admin"],
    )
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码（用于自动化；不提供 --password 以免进入 shell 历史）",
    )

    admin = sub.add_parser("create-admin", help="创建管理员")
    admin.add_argument("--username", required=True)
    admin.add_argument("--display-name", default=None)
    admin.add_argument("--password-stdin", action="store_true")

    reset = sub.add_parser("reset-password", help="重置密码并撤销该用户全部会话")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password-stdin", action="store_true")

    sub.add_parser("list", help="列出用户")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    database = Database(settings.database_url)
    if not database.enabled or database.session_factory is None:
        print(
            "DATABASE_URL 未配置，无法管理用户；请先配置 PostgreSQL。",
            file=sys.stderr,
        )
        return 2
    await database.init(auto_migrate=True)
    if not await database.ping():
        print("无法连接 PostgreSQL，请先运行 start.bat 或启动数据库。", file=sys.stderr)
        await database.close()
        return 3
    service = AuthService(
        AuthRepository(database.session_factory),
        AuthPolicy(
            session_ttl_hours=settings.auth_session_ttl_hours,
            max_failures=settings.auth_login_max_failures,
            lockout_minutes=settings.auth_lockout_minutes,
        ),
    )
    try:
        if args.command == "list":
            users = await service.list_users()
            if not users:
                print("尚无用户；运行 create-admin 创建第一个管理员。")
                return 0
            print(f"{'id':<34} {'username':<24} {'role':<12} status")
            for user in users:
                status = "active" if user.is_active else "disabled"
                print(
                    f"{user.id:<34} {user.username:<24} "
                    f"{user.role:<12} {status}"
                )
            return 0

        if args.command in {"create-user", "create-admin"}:
            password = _read_password(password_stdin=args.password_stdin)
            role = "admin" if args.command == "create-admin" else args.role
            user = await service.create_user(
                args.username,
                password,
                display_name=args.display_name,
                role=role,
            )
            projects = ProjectRepository(database.session_factory)
            await projects.add_default_member(user.id, user.role)
            print(f"已创建用户 {user.username}（{user.role}），id={user.id}")
            return 0

        if args.command == "reset-password":
            target = await service.repo.get_user_by_username(
                normalize_username(args.username)
            )
            if target is None:
                print(f"用户不存在：{args.username}", file=sys.stderr)
                return 1
            password = _read_password(password_stdin=args.password_stdin)
            await service.reset_password(target.id, password)
            print(f"已重置 {target.username} 的密码，并撤销其全部会话。")
            return 0
    except AuthError as exc:
        print(f"操作失败：{exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI reports the failure
        print(f"数据库操作失败：{exc}", file=sys.stderr)
        return 4
    finally:
        await database.close()
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

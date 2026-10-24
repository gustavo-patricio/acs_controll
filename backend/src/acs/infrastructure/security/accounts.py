"""Local bootstrap and credential lifecycle; no public registration endpoint."""

import argparse
import asyncio
import getpass
import json
import re
import sys
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from acs.infrastructure.database.auth_models import AuthAccount
from acs.infrastructure.database.database import Database
from acs.infrastructure.security.passwords import hash_password
from acs.settings import get_settings


def read_password() -> str:
    if not sys.stdin.isatty():
        raise ValueError(
            "Use an interactive terminal; passwords are never accepted in arguments or echoed input"
        )
    password = getpass.getpass("New password: ")
    if len(password) < 14 or len(password.encode("utf-8")) > 1024:
        raise ValueError("Use at least 14 characters and at most 1024 UTF-8 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("Control characters are not accepted")
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords differ")
    return password


async def execute(args: argparse.Namespace, encoded: str | None) -> None:
    settings = get_settings()
    if settings.database_echo or settings.environment not in ("development", "test"):
        raise ValueError(
            "Local accounts are restricted to development/test with SQL echo disabled"
        )
    shared = bool(getattr(args, "shared", False))
    if shared and (args.action != "create" or args.kind != "cpe"):
        raise ValueError("--shared is only valid when creating a CPE account")
    if shared and any((args.oui, args.product_class, args.serial_number)):
        raise ValueError(
            "Shared CPE accounts cannot be bound to an individual identity"
        )
    database = Database(settings)
    try:
        async with database.session() as db, db.begin():
            account = await db.scalar(
                select(AuthAccount)
                .where(
                    AuthAccount.username == args.username,
                    AuthAccount.kind == args.kind,
                )
                .with_for_update()
            )
            if args.action == "create":
                if account is not None:
                    raise ValueError("Account already exists; no changes made")
                if encoded is None:
                    raise ValueError("Password required")
                account = AuthAccount(
                    id=uuid4(),
                    kind=args.kind,
                    username=args.username,
                    password_hash=encoded,
                    enabled=True,
                    shared_cpe=shared,
                    role=args.role if args.kind == "admin" else "device",
                    oui=args.oui,
                    product_class=args.product_class,
                    serial_number=args.serial_number,
                )
                db.add(account)
            elif account is None:
                raise ValueError("Account not found")
            elif args.action == "disable":
                account.enabled = False
            elif args.action == "rotate" and encoded is not None:
                account.password_hash = encoded
            account_id = str(account.id)
        print(
            json.dumps(
                {
                    "event": f"account_{args.action}",
                    "account_id": account_id,
                    "kind": args.kind,
                }
            )
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "disable", "rotate"))
    parser.add_argument("kind", choices=("admin", "cpe"))
    parser.add_argument("username")
    parser.add_argument(
        "--role", choices=("administrator", "support", "pending"), default="pending"
    )
    parser.add_argument("--oui")
    parser.add_argument("--product-class")
    parser.add_argument("--serial-number")
    parser.add_argument(
        "--shared",
        action="store_true",
        help="Create a shared CPE provisioning profile, without an individual device identity",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", args.username):
        parser.error("Username must contain 1-64 ASCII letters, digits, _, ., @ or -")
    if args.shared:
        if args.action != "create" or args.kind != "cpe":
            parser.error("--shared is only valid for create cpe")
        if any((args.oui, args.product_class, args.serial_number)):
            parser.error("Do not combine --shared with device identity fields")
    elif args.action == "create" and args.kind == "cpe":
        if not args.oui or not re.fullmatch(r"[A-Fa-f0-9]{6}", args.oui):
            parser.error("CPE requires a six-digit hexadecimal --oui")
        args.oui = args.oui.upper()
        if any(
            not value or len(value) > 64
            for value in (args.product_class, args.serial_number)
        ):
            parser.error(
                "CPE requires --product-class and --serial-number (1-64 characters)"
            )
    elif any((args.oui, args.product_class, args.serial_number)):
        parser.error("Identity fields are only accepted when creating CPE accounts")
    try:
        encoded = (
            hash_password(read_password())
            if args.action in ("create", "rotate")
            else None
        )
        asyncio.run(execute(args, encoded))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except SQLAlchemyError:
        parser.exit(
            1, "Database operation failed; verify migrations and account uniqueness\n"
        )


if __name__ == "__main__":
    main()

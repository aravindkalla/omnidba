#!/usr/bin/env python3
"""
CLI tool to manage OmniDBA API users.

Usage:
    python create_user.py add    <email> <password> [--role admin|dba]
    python create_user.py list
    python create_user.py reset  <email> <new_password>
    python create_user.py deactivate <email>
    python create_user.py activate   <email>

Passwords are bcrypt-hashed before storing. The plaintext password is
never written to users.json.
"""

import argparse
import sys
from pathlib import Path

# Ensure we're running from the project root
sys.path.insert(0, str(Path(__file__).parent))
from auth import add_user, all_users, update_password, set_active, get_user


def cmd_add(args: argparse.Namespace) -> None:
    try:
        user = add_user(args.email, args.password, role=args.role)
        print(f"[OK] Created: {user.email}  role={user.role}  must_change_password=True")
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_list(_args: argparse.Namespace) -> None:
    users = all_users()
    if not users:
        print("No users found in users.json")
        return
    print(f"{'Email':<40}  {'Role':<8}  {'Active':<8}  Must Change Pwd")
    print("-" * 75)
    for u in users:
        print(f"{u.email:<40}  {u.role:<8}  {'yes' if u.is_active else 'NO':<8}  "
              f"{'yes' if u.must_change_password else 'no'}")


def cmd_reset(args: argparse.Namespace) -> None:
    if not get_user(args.email):
        print(f"[ERROR] User '{args.email}' not found", file=sys.stderr)
        sys.exit(1)
    update_password(args.email, args.new_password)
    print(f"[OK] Password reset for {args.email}")


def cmd_deactivate(args: argparse.Namespace) -> None:
    if not set_active(args.email, False):
        print(f"[ERROR] User '{args.email}' not found", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Deactivated: {args.email}")


def cmd_activate(args: argparse.Namespace) -> None:
    if not set_active(args.email, True):
        print(f"[ERROR] User '{args.email}' not found", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Activated: {args.email}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OmniDBA — user management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a new user")
    p_add.add_argument("email")
    p_add.add_argument("password")
    p_add.add_argument("--role", choices=["admin", "dba"], default="dba")

    sub.add_parser("list", help="List all users")

    p_reset = sub.add_parser("reset", help="Reset a user's password")
    p_reset.add_argument("email")
    p_reset.add_argument("new_password")

    p_deact = sub.add_parser("deactivate", help="Disable a user account")
    p_deact.add_argument("email")

    p_act = sub.add_parser("activate", help="Re-enable a user account")
    p_act.add_argument("email")

    args = parser.parse_args()
    dispatch = {
        "add":        cmd_add,
        "list":       cmd_list,
        "reset":      cmd_reset,
        "deactivate": cmd_deactivate,
        "activate":   cmd_activate,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

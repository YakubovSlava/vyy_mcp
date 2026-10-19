"""CLI for the main admin to issue/revoke child-admin credentials.

Run on the main host (the one with the real shared tools directory):

    jupyter-mcp-tools-admin-users add alice
    jupyter-mcp-tools-admin-users list
    jupyter-mcp-tools-admin-users remove alice

Every admin — including the main admin's own account for logging into
their local admin UI — is created this way; see README.md.
"""
from __future__ import annotations

import argparse
import secrets
import sys

from ..common import auth


def _cmd_add(args: argparse.Namespace) -> None:
    password = args.password or secrets.token_urlsafe(12)
    auth.add_admin(args.username, password)
    print(f"Added admin '{args.username}'.")
    if not args.password:
        print(f"Generated password: {password}")
    print("Give these credentials to the admin out-of-band (they are not stored anywhere else).")


def _cmd_remove(args: argparse.Namespace) -> None:
    if auth.remove_admin(args.username):
        print(f"Removed admin '{args.username}'.")
    else:
        print(f"No such admin: '{args.username}'.", file=sys.stderr)
        sys.exit(1)


def _cmd_list(args: argparse.Namespace) -> None:
    admins = auth.list_admins()
    if not admins:
        print("No admins configured yet. Add one with: jupyter-mcp-tools-admin-users add <username>")
        return
    for username in admins:
        print(username)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage jupyter-mcp-tools admin accounts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add or reset an admin's password")
    add_parser.add_argument("username")
    add_parser.add_argument("--password", help="Set an explicit password instead of generating one")
    add_parser.set_defaults(func=_cmd_add)

    remove_parser = subparsers.add_parser("remove", help="Revoke an admin's access")
    remove_parser.add_argument("username")
    remove_parser.set_defaults(func=_cmd_remove)

    list_parser = subparsers.add_parser("list", help="List configured admin usernames")
    list_parser.set_defaults(func=_cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

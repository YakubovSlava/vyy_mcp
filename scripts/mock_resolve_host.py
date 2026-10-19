#!/usr/bin/env python3
"""Placeholder for the real Bitbucket host-lookup script.

Calling convention (see README.md -> "Onboarding end users" and
PLAN.md): takes a username as argv[1], a password on stdin (ignored
here), and must print the current MCP host address to stdout.

This mock ignores the credentials entirely and always returns the local
MCP server's address, so the end-user connection notebook
(admin_ui/notebook_builder.py) can be exercised end-to-end before the
real Bitbucket-backed script exists. Point the admin UI's "Bitbucket
host-lookup script path" at this file's absolute path to try the flow
locally, then swap it for the real script later — nothing else needs to
change.
"""
from __future__ import annotations

import sys

MOCK_HOST_ADDRESS = "http://127.0.0.1:8765"


def main() -> None:
    _username = sys.argv[1] if len(sys.argv) > 1 else None
    _password = sys.stdin.read()  # ignored by the mock
    print(MOCK_HOST_ADDRESS)


if __name__ == "__main__":
    main()

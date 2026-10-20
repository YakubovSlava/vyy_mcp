#!/usr/bin/env python3
"""GIT_ASKPASS helper for common/git_push.py.

Git invokes this with the prompt text ("Username for '...':" or "Password
for '...':") as argv[1] and expects the answer on stdout. Reading the
answer from GIT_PUSH_USERNAME/GIT_PUSH_PASSWORD env vars instead of a CLI
argument means the admin-configured git password never shows up in `ps
aux` output, unlike embedding it directly in the push command/URL.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    if "username" in prompt.lower():
        print(os.environ.get("GIT_PUSH_USERNAME", ""))
    else:
        print(os.environ.get("GIT_PUSH_PASSWORD", ""))


if __name__ == "__main__":
    main()

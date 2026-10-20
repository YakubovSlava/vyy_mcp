# Architecture notes

Two long-running processes share one on-disk directory
(`shared_tools/tools/<name>/`):

1. The **MCP server** polls that directory every few seconds and
   registers/unregisters MCP tools to match what's installed.
2. The **admin UI** reads/writes the same directory when an admin
   uploads, removes, or lists skills.

Two kinds of skill bundle can live in that directory side by side:

- **Code skills** — a single self-describing `.py` file (its one
  top-level function's name/docstring/type hints are the manifest; no
  separate `manifest.json`).
- **Doc skills** — `SKILL.md` (this skill is one) + any supporting
  files, following the same shape as Claude's "Agent Skills" format.

---
name: repo-explainer
description: Use this when the user asks how this repository is organized, where a particular piece of functionality lives, or wants an onboarding overview before making a change. Explains the repo's structure and points at the right files.
---

# Repo explainer

This repository hosts an MCP server and an admin web UI for sharing
tools ("skills") with everyone using a JupyterLab/JupyterHub server.

## Layout

- `jupyter_mcp_tools/common/` — shared logic: manifest/doc-skill
  validation, the on-disk registry, admin auth, config.
- `jupyter_mcp_tools/mcp_server/` — the MCP server itself.
- `jupyter_mcp_tools/admin_ui/` — the FastAPI admin web app.
- `examples/` — sample skill bundles.
- `scripts/` — standalone operational scripts (child-admin launcher,
  notebook builder, diagnostics).

See `references/architecture.md` for more detail on how the pieces fit
together, or call this skill again with
`file="references/architecture.md"` to read it directly.

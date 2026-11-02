import asyncio
import json
import zipfile
from pathlib import Path

from jupyter_mcp_tools.common import registry
from jupyter_mcp_tools.mcp_server.server import build_app, sync_tools
from jupyter_mcp_tools.tests.conftest import VALID_MODULE

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def _zip_example(name: str, dest: Path) -> Path:
    """Archives aren't committed to the repo (see .gitignore) — zip the
    example's source directly for the test."""
    src = EXAMPLES_DIR / name
    archive = dest / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(src.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(src))
    return archive


def test_build_app_defaults_to_default_instructions(shared_dir):
    from jupyter_mcp_tools.common.config import DEFAULT_INSTRUCTIONS

    app = build_app(shared_dir)
    assert app.instructions == DEFAULT_INSTRUCTIONS


def test_sync_picks_up_custom_instructions_live(shared_dir):
    from jupyter_mcp_tools.common.config import AppConfig, save_config

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    save_config(AppConfig(shared_dir=str(shared_dir), instructions="Custom live instructions."))
    sync_tools(app, shared_dir)

    assert app.instructions == "Custom live instructions."


def test_sync_registers_and_removes_tools(make_archive, shared_dir):
    app = build_app(shared_dir)

    sync_tools(app, shared_dir)
    # Only the built-in helper is there before anything is installed.
    assert {t.name for t in app._tool_manager.list_tools()} == {"skill_builder"}

    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    sync_tools(app, shared_dir)

    tools = app._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert "greet" in names and "skill_builder" in names

    content, _ = asyncio.run(app.call_tool("greet", {"name": "World"}))
    assert "World" in content[0].text

    registry.remove_tool(shared_dir, "greet")
    sync_tools(app, shared_dir)
    # Removing the installed skill leaves only the built-in helper.
    assert {t.name for t in app._tool_manager.list_tools()} == {"skill_builder"}


def test_sync_picks_up_updated_tool_code(make_archive, shared_dir):
    app = build_app(shared_dir)

    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    sync_tools(app, shared_dir)

    updated_module = VALID_MODULE.replace("Hello", "Hi")
    archive2 = make_archive("greet2", updated_module)
    registry.install_tool(archive2, shared_dir, overwrite=True)
    sync_tools(app, shared_dir)

    content, _ = asyncio.run(app.call_tool("greet", {"name": "World"}))
    assert content[0].text == "Hi, World!"


def test_multi_file_skill_result_round_trip(shared_dir, tmp_path):
    """The examples/setup_files_tool skill returns multiple files for a
    human to run themselves (common/skill_results.py). This exercises the
    real, dynamically-loaded example — not a synthetic stand-in — the
    same way the MCP server itself loads skills."""
    archive = _zip_example("setup_files_tool", tmp_path)
    registry.install_tool(archive, shared_dir)

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    result = asyncio.run(app.call_tool("make_setup_files", {"project_name": "demo"}))
    content = result[0] if isinstance(result, tuple) else result
    payload = json.loads(content[0].text)

    assert payload["message"].startswith("Setup files for 'demo'")
    filenames = [f["filename"] for f in payload["files"]]
    assert filenames == ["setup.sh", "config.yaml"]
    assert "demo" in payload["files"][0]["content"]
    assert payload["files"][0]["run_hint"] == "bash setup.sh"


def test_doc_skill_round_trip(shared_dir, tmp_path):
    """examples/repo_explainer is a SKILL.md-based doc skill (no Python
    entrypoint). Exercises the real example through install -> sync ->
    call, both the no-argument (main doc + file listing) and file=
    (specific file content) cases, the same way the MCP server loads it."""
    archive = _zip_example("repo_explainer", tmp_path)
    registry.install_tool(
        archive,
        shared_dir,
        name="repo-explainer",
        description="Use this when the user asks how this repository is organized.",
    )

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    tools = app._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert "repo-explainer" in names and "skill_builder" in names
    assert "organized" in tools[0].description

    content, _ = asyncio.run(app.call_tool("repo-explainer", {}))
    assert "Repo explainer" in content[0].text
    assert "references/architecture.md" in content[0].text

    content, _ = asyncio.run(app.call_tool("repo-explainer", {"file": "references/architecture.md"}))
    assert "Architecture notes" in content[0].text

    content, _ = asyncio.run(app.call_tool("repo-explainer", {"file": "nope.md"}))
    assert "not a file in this skill" in content[0].text


def test_sync_prefixes_description_with_group(make_archive, shared_dir):
    app = build_app(shared_dir)

    module = '__group__ = "networking"\n\n' + VALID_MODULE
    archive = make_archive("greet", module)
    registry.install_tool(archive, shared_dir)
    sync_tools(app, shared_dir)

    tools = app._tool_manager.list_tools()
    # Marker first, then the group prefix: "does this run code" is the more
    # consequential fact for an agent than which category it belongs to.
    assert tools[0].description.startswith("[exec] [networking] ")


def test_sync_no_group_prefix_when_group_is_empty(make_archive, shared_dir):
    app = build_app(shared_dir)

    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    sync_tools(app, shared_dir)

    tools = app._tool_manager.list_tools()
    # An ungrouped code skill starts with the exec marker and nothing else.
    assert tools[0].description.startswith("[exec] ")
    assert "[networking]" not in tools[0].description


def test_sync_folds_grouped_skills_behind_one_dispatcher_tool(make_archive, shared_dir, tmp_path):
    """A skill whose `group` matches a *created* group (common/groups.py)
    must not appear as its own tool in tools/list at all -- only the
    group's own dispatcher tool shows up, with the group's own
    description, per explicit request that grouping actually shrink
    tools/list rather than just tag it."""
    from jupyter_mcp_tools.common import groups

    groups.create_group(shared_dir, "networking", "Network-related skills.")

    code_module = '__group__ = "networking"\n\n' + VALID_MODULE
    registry.install_tool(make_archive("greet", code_module), shared_dir)

    doc_archive = tmp_path / "doc.zip"
    with zipfile.ZipFile(doc_archive, "w") as zf:
        zf.writestr("SKILL.md", "# Ping\n\nPings a host.\n")
    registry.install_tool(doc_archive, shared_dir, name="ping", description="Pings a host.", group="networking")

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    names = {t.name for t in app._tool_manager.list_tools()}
    # The two grouped skills are folded behind one dispatcher; the built-in
    # helper is ungrouped so it stays a tool of its own.
    assert names == {"group_networking", "skill_builder"}

    group_tool = next(t for t in app._tool_manager.list_tools() if t.name == "group_networking")
    assert group_tool.description == "Network-related skills."

    # No-argument call lists both skills with an argument hint each.
    content, _ = asyncio.run(app.call_tool("group_networking", {}))
    listing = content[0].text
    assert "greet" in listing and "ping" in listing
    assert "name: str" in listing  # greet's own parameter, surfaced since it has no tool of its own
    # A group can mix both kinds, so the group tool carries no blanket
    # marker -- each skill in the listing is marked individually.
    assert "[exec] greet" in listing
    assert "[read] ping" in listing
    assert not group_tool.description.startswith("[exec]")

    # Dispatching to the code skill actually runs it.
    content, _ = asyncio.run(app.call_tool("group_networking", {"skill": "greet", "arguments": {"name": "World"}}))
    assert "World" in content[0].text

    # Dispatching to the doc skill returns its progressive-disclosure body.
    content, _ = asyncio.run(app.call_tool("group_networking", {"skill": "ping"}))
    assert "Pings a host" in content[0].text

    # An unknown skill name inside a known group is a clean error, not a crash.
    content, _ = asyncio.run(app.call_tool("group_networking", {"skill": "nope"}))
    assert "not a skill in this group" in content[0].text


def test_sync_falls_back_to_standalone_for_unrecognized_group(make_archive, shared_dir):
    # A skill tagged with a group that was never actually created (see
    # common/groups.py) must still register as its own tool, same as no
    # group at all -- it just doesn't get hidden.
    module = '__group__ = "nonexistent"\n\n' + VALID_MODULE
    archive = make_archive("greet", module)
    registry.install_tool(archive, shared_dir)

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    tools = app._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert "greet" in names and "skill_builder" in names
    assert tools[0].description.startswith("[exec] [nonexistent] ")


# --------------------------------------------------------------------------
# per-tool markers: how an agent learns whether a tool executes anything
# --------------------------------------------------------------------------


def test_code_skill_is_marked_executable(make_archive, shared_dir):
    """The point of the marker: an agent must be able to tell from
    `tools/list` alone that calling this runs real code on the server."""
    from jupyter_mcp_tools.mcp_server.server import EXEC_MARKER

    registry.install_tool(make_archive("greet", VALID_MODULE), shared_dir)
    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    description = app._tool_manager.list_tools()[0].description
    assert description.startswith(EXEC_MARKER)
    # The skill's own description must survive intact after the marker.
    assert "Greets someone by name." in description


def test_doc_skill_is_marked_read_only(shared_dir, tmp_path):
    """Deliberately NOT marked executable: a doc skill only returns text, so
    a blanket "everything here is executable" would mislead an agent into
    trusting it as a way to run a script."""
    from jupyter_mcp_tools.mcp_server.server import READ_MARKER

    doc_archive = tmp_path / "doc.zip"
    with zipfile.ZipFile(doc_archive, "w") as zf:
        zf.writestr("SKILL.md", "# Guide\n\nInstructions only.\n")
    registry.install_tool(
        doc_archive, shared_dir, name="guide", description="Explains the thing."
    )

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    description = app._tool_manager.list_tools()[0].description
    assert description.startswith(READ_MARKER)
    assert "Explains the thing." in description


def test_markers_are_mutually_exclusive_and_always_present(make_archive, shared_dir, tmp_path):
    from jupyter_mcp_tools.mcp_server.server import EXEC_MARKER, READ_MARKER

    registry.install_tool(make_archive("greet", VALID_MODULE), shared_dir)
    doc_archive = tmp_path / "doc.zip"
    with zipfile.ZipFile(doc_archive, "w") as zf:
        zf.writestr("SKILL.md", "# Guide\n")
    registry.install_tool(doc_archive, shared_dir, name="guide", description="Explains.")

    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    for tool in app._tool_manager.list_tools():
        has_exec = tool.description.startswith(EXEC_MARKER)
        has_read = tool.description.startswith(READ_MARKER)
        assert has_exec != has_read, f"{tool.name} carries neither or both markers"


def test_instructions_explain_the_markers(shared_dir):
    """A marker nobody explains is just noise; the server's `instructions`
    field is the one thing every connecting agent reads automatically."""
    app = build_app(shared_dir)
    sync_tools(app, shared_dir)

    instructions = app.instructions
    assert "[exec]" in instructions
    assert "[read]" in instructions
    assert "files" in instructions  # the "hand these to the human" caveat

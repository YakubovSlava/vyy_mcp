"""Renaming an installed skill.

The name is not a label: it's the folder on disk, the MCP tool name an agent
sees, and for a code skill also the entrypoint function's own name. These
tests pin that all three move together, that a failed rename leaves the
original intact, and that nothing else in the bundle is lost.
"""
import zipfile

import pytest

from jupyter_mcp_tools.common import registry
from jupyter_mcp_tools.common.manifest import ToolValidationError
from jupyter_mcp_tools.common.registry import ToolAlreadyExistsError, ToolNotFoundError
from jupyter_mcp_tools.tests.test_admin_api import (  # noqa: F401
    ADMIN_PASSWORD,
    ADMIN_USER,
    anon_client as admin_anon_client,
    app_module,  # noqa: F401
    client as admin_client,
    read_only_client,
)

CODE = '''"""Module docstring."""

# A comment that must survive the rename.

def old_name(text: str, shout: bool = False) -> str:
    """Does the thing. Use when the user asks for the thing."""
    return text.upper() if shout else text  # inline note
'''


def _install_code(shared, module_source=CODE, helper=False):
    archive = shared.parent / "code.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool.py", module_source)
        if helper:
            zf.writestr("data.json", '{"k": 1}\n')
    return registry.install_tool(archive, shared)


# --------------------------------------------------------------------------
# code skills
# --------------------------------------------------------------------------


def test_renames_folder_function_and_tool_name(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)

    manifest = registry.rename_tool(shared, "old_name", "new_name")

    # All three names move: the folder, the function, and therefore the tool.
    assert manifest.name == "new_name"
    assert manifest.function_name == "new_name"
    assert (shared / "tools" / "new_name").is_dir()
    assert not (shared / "tools" / "old_name").exists()
    assert [m.name for m in registry.list_tools(shared)] == ["new_name"]


def test_rename_preserves_comments_and_formatting(tmp_path):
    """A targeted source edit, not `ast.unparse` — reformatting the whole
    file would make the diff unreadable and could drop meaningful layout."""
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    registry.rename_tool(shared, "old_name", "new_name")

    source = (shared / "tools" / "new_name" / "tool.py").read_text()
    assert "# A comment that must survive the rename." in source
    assert "# inline note" in source
    assert '"""Module docstring."""' in source
    assert "def new_name(" in source
    assert "def old_name(" not in source


def test_rename_keeps_auxiliary_files(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared, helper=True)
    registry.rename_tool(shared, "old_name", "new_name")
    assert (shared / "tools" / "new_name" / "data.json").read_text() == '{"k": 1}\n'


def test_renamed_skill_still_loads_and_runs(tmp_path):
    """The real proof: the loader must find the module under the new name."""
    from jupyter_mcp_tools.common.loader import load_entrypoint

    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    manifest = registry.rename_tool(shared, "old_name", "new_name")

    fn = load_entrypoint(shared, manifest)
    assert fn("hi", shout=True) == "HI"


def test_rejects_a_name_that_already_exists(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    registry.create_doc_skill(shared, name="taken", description="d", body="# x\n")

    with pytest.raises(ToolAlreadyExistsError):
        registry.rename_tool(shared, "old_name", "taken")
    # Nothing moved.
    assert (shared / "tools" / "old_name").is_dir()


def test_rejects_a_missing_skill(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with pytest.raises(ToolNotFoundError):
        registry.rename_tool(shared, "nope", "other")


def test_rejects_an_invalid_name(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    with pytest.raises(ToolValidationError):
        registry.rename_tool(shared, "old_name", "Not A Slug")
    assert (shared / "tools" / "old_name").is_dir()


def test_failed_rename_leaves_the_original_untouched(tmp_path):
    """A rename that would produce an unloadable module must abort, not
    leave a half-moved skill behind."""
    shared = tmp_path / "shared"
    shared.mkdir()
    # A module whose single top-level function isn't the one being renamed
    # can't be renamed coherently.
    _install_code(shared, module_source='def old_name(x: str) -> str:\n    """D."""\n    return x\n')

    # Renaming to a name that the validator rejects after the edit.
    with pytest.raises(ToolValidationError):
        registry.rename_tool(shared, "old_name", "x")

    assert (shared / "tools" / "old_name" / "tool.py").exists()
    assert not (shared / "tools" / "x").exists()


# --------------------------------------------------------------------------
# doc skills
# --------------------------------------------------------------------------


def test_renames_a_doc_skill_and_keeps_body_and_meta(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    registry.create_doc_skill(shared, name="docold", description="Explains.", body="# Body\n", group="g")

    manifest = registry.rename_tool(shared, "docold", "docnew")

    assert manifest.name == "docnew"
    assert manifest.body.strip() == "# Body"
    # Description/group live in the sidecar and must survive the move.
    assert manifest.description == "Explains."
    assert manifest.group == "g"
    assert not (shared / "tools" / "docold").exists()


def test_rename_keeps_a_doc_skills_extra_files(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    registry.create_doc_skill(shared, name="docold", description="d", body="# B\n")
    (shared / "tools" / "docold" / "refs").mkdir()
    (shared / "tools" / "docold" / "refs" / "a.md").write_text("x\n")

    registry.rename_tool(shared, "docold", "docnew")
    assert (shared / "tools" / "docnew" / "refs" / "a.md").read_text() == "x\n"


# --------------------------------------------------------------------------
# admin UI
# --------------------------------------------------------------------------


def test_rename_via_the_ui(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    _install_code(shared)

    r = admin_client.post("/tools/old_name/rename", data={"new_name": "new_name"})
    assert "переименован" in r.text
    assert (shared / "tools" / "new_name" / "tool.py").exists()


def test_rename_to_the_same_name_is_a_no_op(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    _install_code(shared)

    r = admin_client.post("/tools/old_name/rename", data={"new_name": "old_name"})
    assert "не изменилось" in r.text
    assert (shared / "tools" / "old_name").exists()


def test_rename_shows_a_friendly_error_for_a_taken_name(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    _install_code(shared)
    registry.create_doc_skill(shared, name="taken", description="d", body="# x\n")

    r = admin_client.post("/tools/old_name/rename", data={"new_name": "taken"})
    assert "уже существует" in r.text


def test_rename_refused_for_a_builtin(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    r = admin_client.post("/tools/skill_builder/rename", data={"new_name": "mine"})
    assert "встроенный" in r.text.lower()
    assert (shared / "tools" / "skill_builder").exists()


def test_rename_blocked_in_read_only_mode(read_only_client):
    r = read_only_client.post("/tools/x/rename", data={"new_name": "y"})
    assert "только просмотр" in r.text.lower()


def test_api_rename_round_trip(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    _install_code(shared)

    r = admin_client.post(
        "/api/tools/old_name/rename", auth=(ADMIN_USER, ADMIN_PASSWORD), data={"new_name": "fresh"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "fresh"


def test_rename_button_is_relative_and_present(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    _install_code(shared)

    html = admin_client.get("/").text
    assert "openRenameSkill" in html
    assert 'action="tools/{{' not in html  # action is set by JS, relative
    assert '"tools/" + encodeURIComponent(name) + "/rename"' in html


def test_staging_directory_does_not_survive_a_rename(tmp_path):
    """Regression found via the health check: a leaked `.rename-*` folder
    showed up as a skill directory that the server never registered."""
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    registry.rename_tool(shared, "old_name", "new_name")

    leftovers = [p.name for p in (shared / "tools").iterdir() if p.name.startswith(".rename-")]
    assert leftovers == []
    # And the rename itself still worked.
    assert (shared / "tools" / "new_name" / "tool.py").exists()


def test_dot_directories_are_never_listed_as_skills(tmp_path):
    """A crash mid-install/rename must not surface scratch space as a skill."""
    shared = tmp_path / "shared"
    shared.mkdir()
    _install_code(shared)
    (shared / "tools" / ".rename-crashed").mkdir()
    (shared / "tools" / ".rename-crashed" / "tool.py").write_text(
        'def ghost(x: str) -> str:\n    """D."""\n    return x\n'
    )

    assert [m.name for m in registry.list_tools(shared)] == ["old_name"]

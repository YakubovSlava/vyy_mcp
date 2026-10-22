import zipfile

import pytest

from jupyter_mcp_tools.common import registry
from jupyter_mcp_tools.common.doc_skill import DocSkillManifest
from jupyter_mcp_tools.common.loader import load_entrypoint
from jupyter_mcp_tools.common.manifest import ToolValidationError
from jupyter_mcp_tools.common.registry import ToolAlreadyExistsError, ToolNotFoundError
from jupyter_mcp_tools.tests.conftest import VALID_MODULE

VALID_SKILL_MD = """# Repo explainer

Body content.
"""


def _make_doc_skill_archive(tmp_path, name="repo-explainer", skill_md=VALID_SKILL_MD, extra_files=None):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        for path, content in (extra_files or {}).items():
            zf.writestr(path, content)
    return archive


def _install_doc_skill(archive, shared_dir, *, name="repo-explainer", description="Use this when the user asks about the repo's structure.", **kwargs):
    return registry.install_tool(archive, shared_dir, name=name, description=description, **kwargs)


def test_install_list_remove_round_trip(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MODULE)

    manifest = registry.install_tool(archive, shared_dir)
    assert manifest.name == "greet"

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["greet"]

    fn = load_entrypoint(shared_dir, manifest)
    assert fn("World") == "Hello, World!"

    registry.remove_tool(shared_dir, "greet")
    assert registry.list_tools(shared_dir) == []


def test_install_duplicate_without_overwrite_raises(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    with pytest.raises(ToolAlreadyExistsError):
        registry.install_tool(archive, shared_dir)


def test_install_duplicate_with_overwrite_replaces(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)

    updated_module = VALID_MODULE.replace("Hello", "Hi")
    archive2 = make_archive("greet2", updated_module)
    manifest = registry.install_tool(archive2, shared_dir, overwrite=True)

    fn = load_entrypoint(shared_dir, manifest)
    assert fn("World") == "Hi, World!"


def test_remove_missing_tool_raises(shared_dir):
    with pytest.raises(ToolNotFoundError):
        registry.remove_tool(shared_dir, "nope")


def test_list_tools_empty_dir(shared_dir):
    assert registry.list_tools(shared_dir) == []


def test_list_tools_skips_corrupted_entries(shared_dir):
    bad = shared_dir / "tools" / "broken"
    bad.mkdir(parents=True)
    (bad / "tool.py").write_text("def greet(name:\n")  # syntax error
    assert registry.list_tools(shared_dir) == []


def test_install_and_list_doc_skill(tmp_path, shared_dir):
    archive = _make_doc_skill_archive(tmp_path, extra_files={"references/notes.md": "notes"})

    manifest = _install_doc_skill(archive, shared_dir)
    assert isinstance(manifest, DocSkillManifest)
    assert manifest.name == "repo-explainer"

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["repo-explainer"]
    assert isinstance(tools[0], DocSkillManifest)
    assert tools[0].description == "Use this when the user asks about the repo's structure."
    assert tools[0].files == ("references/notes.md",)


def test_doc_skill_install_without_name_or_description_raises(tmp_path, shared_dir):
    archive = _make_doc_skill_archive(tmp_path)
    with pytest.raises(ToolValidationError, match="needs a name"):
        registry.install_tool(archive, shared_dir)
    with pytest.raises(ToolValidationError, match="needs a description"):
        registry.install_tool(archive, shared_dir, name="repo-explainer")


def test_code_and_doc_skills_coexist(tmp_path, make_archive, shared_dir):
    code_archive = make_archive("greet", VALID_MODULE)
    doc_archive = _make_doc_skill_archive(tmp_path)

    registry.install_tool(code_archive, shared_dir)
    _install_doc_skill(doc_archive, shared_dir)

    names = sorted(t.name for t in registry.list_tools(shared_dir))
    assert names == ["greet", "repo-explainer"]


def test_install_archive_without_py_or_skill_md_raises(tmp_path, shared_dir):
    archive = tmp_path / "neither.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ToolValidationError, match="SKILL.md .* or a single .py file"):
        registry.install_tool(archive, shared_dir)


def test_manifest_to_dict_works_for_doc_skill(tmp_path, shared_dir):
    archive = _make_doc_skill_archive(tmp_path)
    manifest = _install_doc_skill(archive, shared_dir)
    d = registry.manifest_to_dict(manifest)
    assert d["name"] == "repo-explainer"
    assert "body" in d


def test_update_doc_skill_description(tmp_path, shared_dir):
    archive = _make_doc_skill_archive(tmp_path)
    _install_doc_skill(archive, shared_dir)

    manifest = registry.update_doc_skill_description(shared_dir, "repo-explainer", "New description.")
    assert manifest.description == "New description."

    tools = registry.list_tools(shared_dir)
    assert tools[0].description == "New description."
    # The body/files must survive the edit untouched.
    assert tools[0].body == "# Repo explainer\n\nBody content."


def test_update_doc_skill_description_missing_tool_raises(shared_dir):
    with pytest.raises(ToolNotFoundError):
        registry.update_doc_skill_description(shared_dir, "nope", "desc")


def test_update_doc_skill_description_empty_raises(tmp_path, shared_dir):
    archive = _make_doc_skill_archive(tmp_path)
    _install_doc_skill(archive, shared_dir)
    with pytest.raises(ToolValidationError, match="must not be empty"):
        registry.update_doc_skill_description(shared_dir, "repo-explainer", "")


def test_update_doc_skill_description_rejects_code_skill(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    with pytest.raises(ToolValidationError, match="not a doc skill"):
        registry.update_doc_skill_description(shared_dir, "greet", "desc")


def test_create_doc_skill_from_text(shared_dir):
    manifest = registry.create_doc_skill(
        shared_dir, name="my-skill", description="Use this skill when...", body="# My Skill\n\nDo the thing.\n"
    )
    assert manifest.name == "my-skill"
    assert manifest.description == "Use this skill when..."
    assert manifest.body == "# My Skill\n\nDo the thing."

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["my-skill"]
    assert tools[0].description == "Use this skill when..."


def test_create_doc_skill_duplicate_without_overwrite_raises(shared_dir):
    registry.create_doc_skill(shared_dir, name="my-skill", description="desc", body="body text")
    with pytest.raises(ToolAlreadyExistsError):
        registry.create_doc_skill(shared_dir, name="my-skill", description="desc2", body="body text 2")


def test_create_doc_skill_duplicate_with_overwrite_replaces(shared_dir):
    registry.create_doc_skill(shared_dir, name="my-skill", description="desc", body="body text")
    manifest = registry.create_doc_skill(
        shared_dir, name="my-skill", description="new desc", body="new body", overwrite=True
    )
    assert manifest.description == "new desc"
    assert manifest.body == "new body"


def test_create_doc_skill_invalid_name_raises(shared_dir):
    with pytest.raises(ToolValidationError, match="must match"):
        registry.create_doc_skill(shared_dir, name="Not Valid", description="desc", body="body")


def test_create_doc_skill_empty_body_raises(shared_dir):
    with pytest.raises(ToolValidationError, match="no content"):
        registry.create_doc_skill(shared_dir, name="my-skill", description="desc", body="   ")


def test_install_doc_skill_archive_with_wrapping_folder(tmp_path, shared_dir):
    # A zip of a *folder* (e.g. right-click "Compress" in a file manager,
    # or `zip -r x.zip myskill/`) nests everything one level deep --
    # "myskill/SKILL.md" instead of "SKILL.md" at the root. Must still
    # install correctly, stripping that wrapping folder automatically.
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("myskill/SKILL.md", "# Wrapped skill\n\nBody.\n")
        zf.writestr("myskill/references/notes.md", "notes")

    manifest = registry.install_tool(archive, shared_dir, name="repo-explainer", description="desc")
    assert manifest.files == ("references/notes.md",)

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["repo-explainer"]
    assert tools[0].files == ("references/notes.md",)


def test_install_code_skill_archive_with_wrapping_folder(tmp_path, shared_dir):
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mytool/tool.py", VALID_MODULE)

    manifest = registry.install_tool(archive, shared_dir)
    assert manifest.name == "greet"

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["greet"]


def test_install_archive_without_wrapping_folder_still_works(tmp_path, shared_dir):
    # A root-level file alongside a nested one must NOT be treated as a
    # wrapping folder (ambiguous) -- the existing "flat" shape must keep
    # working exactly as before.
    archive = tmp_path / "flat.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool.py", VALID_MODULE)

    manifest = registry.install_tool(archive, shared_dir)
    assert manifest.name == "greet"

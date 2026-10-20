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

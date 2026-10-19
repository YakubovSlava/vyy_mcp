import pytest

from jupyter_mcp_tools.common import registry
from jupyter_mcp_tools.common.loader import load_entrypoint
from jupyter_mcp_tools.common.registry import ToolAlreadyExistsError, ToolNotFoundError
from jupyter_mcp_tools.tests.conftest import VALID_MANIFEST, VALID_MODULE


def test_install_list_remove_round_trip(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MANIFEST, VALID_MODULE)

    manifest = registry.install_tool(archive, shared_dir)
    assert manifest.name == "greet"

    tools = registry.list_tools(shared_dir)
    assert [t.name for t in tools] == ["greet"]

    fn = load_entrypoint(shared_dir, manifest)
    assert fn("World") == "Hello, World!"

    registry.remove_tool(shared_dir, "greet")
    assert registry.list_tools(shared_dir) == []


def test_install_duplicate_without_overwrite_raises(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MANIFEST, VALID_MODULE)
    registry.install_tool(archive, shared_dir)
    with pytest.raises(ToolAlreadyExistsError):
        registry.install_tool(archive, shared_dir)


def test_install_duplicate_with_overwrite_replaces(make_archive, shared_dir):
    archive = make_archive("greet", VALID_MANIFEST, VALID_MODULE)
    registry.install_tool(archive, shared_dir)

    updated_module = VALID_MODULE.replace("Hello", "Hi")
    archive2 = make_archive("greet2", VALID_MANIFEST, updated_module)
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
    (bad / "manifest.json").write_text("not json")
    assert registry.list_tools(shared_dir) == []

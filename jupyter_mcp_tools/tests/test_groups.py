import pytest

from jupyter_mcp_tools.common import groups
from jupyter_mcp_tools.common.manifest import ToolValidationError


def test_create_and_list_group(shared_dir):
    group = groups.create_group(shared_dir, "networking", "Network-related skills.")
    assert group.name == "networking"
    assert group.description == "Network-related skills."

    found = groups.list_groups(shared_dir)
    assert [g.name for g in found] == ["networking"]
    assert found[0].description == "Network-related skills."


def test_list_groups_empty(shared_dir):
    assert groups.list_groups(shared_dir) == []


def test_get_group_missing_returns_none(shared_dir):
    assert groups.get_group(shared_dir, "nope") is None


def test_create_duplicate_without_overwrite_raises(shared_dir):
    groups.create_group(shared_dir, "networking", "desc")
    with pytest.raises(groups.GroupAlreadyExistsError):
        groups.create_group(shared_dir, "networking", "desc2")


def test_create_duplicate_with_overwrite_replaces(shared_dir):
    groups.create_group(shared_dir, "networking", "desc")
    group = groups.create_group(shared_dir, "networking", "new desc", overwrite=True)
    assert group.description == "new desc"


def test_create_group_invalid_name_raises(shared_dir):
    with pytest.raises(ToolValidationError, match="must match"):
        groups.create_group(shared_dir, "Not Valid", "desc")


def test_create_group_empty_description_raises(shared_dir):
    with pytest.raises(ToolValidationError, match="needs a description"):
        groups.create_group(shared_dir, "networking", "")


def test_delete_group(shared_dir):
    groups.create_group(shared_dir, "networking", "desc")
    groups.delete_group(shared_dir, "networking")
    assert groups.list_groups(shared_dir) == []


def test_delete_missing_group_raises(shared_dir):
    with pytest.raises(groups.GroupNotFoundError):
        groups.delete_group(shared_dir, "nope")


def test_groups_sorted_by_name(shared_dir):
    groups.create_group(shared_dir, "zebra", "z")
    groups.create_group(shared_dir, "alpha", "a")
    assert [g.name for g in groups.list_groups(shared_dir)] == ["alpha", "zebra"]

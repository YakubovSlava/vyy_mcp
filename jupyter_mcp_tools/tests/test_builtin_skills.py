"""Built-in skills: present by default, restored if deleted, not editable.

The point of these is that an agent never has to guess at the archive
format — it can always ask the built-in helper. That promise only holds if
the skill can't be removed, so most of this file is about restoration and
the admin-UI guards around it.
"""
import re

import pytest

from jupyter_mcp_tools.common import builtin_skills, registry
from jupyter_mcp_tools.common.builtin_skills import (
    BUILTIN_SKILL_NAMES,
    ensure_builtin_skills,
    is_builtin,
)
from jupyter_mcp_tools.tests.test_admin_api import (  # noqa: F401
    ADMIN_PASSWORD,
    ADMIN_USER,
    anon_client as admin_anon_client,
    app_module,  # noqa: F401
    client as admin_client,
    read_only_client,
)


# --------------------------------------------------------------------------
# the skill itself
# --------------------------------------------------------------------------


def test_a_builtin_skill_exists_and_is_a_doc_skill():
    assert BUILTIN_SKILL_NAMES, "there should be at least one built-in skill"
    manifest = registry.list_tools  # just to keep the import used
    assert manifest is not None
    for skill in builtin_skills.BUILTIN_SKILLS:
        assert skill.kind == "doc"
        assert skill.name == skill.name.lower()
        assert skill.description, "an agent decides whether to call it from this"


def test_the_helper_covers_both_kinds_and_their_traps():
    """It exists to stop the agent guessing, so it has to state the rules
    that actually bounce an upload rather than a generic "write a skill"."""
    body = next(s for s in builtin_skills.BUILTIN_SKILLS if s.name == "skill_builder").body
    # Both kinds and the argument that selects them.
    assert "`doc`" in body and "`code`" in body
    assert "create_skill" in body
    # The requirements a code skill is actually validated against.
    assert "docstring" in body.lower()
    assert "ровно одна" in body.lower()
    # The trap that passes validation and fails at call time.
    assert "sys.path" in body
    # And that it's live after a few seconds, without a restart.
    assert "перезапуск" in body.lower()


# --------------------------------------------------------------------------
# presence and self-healing
# --------------------------------------------------------------------------


def test_installed_automatically(tmp_path):
    written = ensure_builtin_skills(tmp_path)
    assert set(written) == BUILTIN_SKILL_NAMES
    for name in BUILTIN_SKILL_NAMES:
        assert (tmp_path / "tools" / name / "SKILL.md").is_file()
        assert (tmp_path / "tools" / name / ".skill_meta.json").is_file()


def test_idempotent_and_creates_no_spurious_changes(tmp_path):
    """Called on every sync tick and every admin page render, so a second
    call must not rewrite anything (that would manufacture a git commit
    every five seconds)."""
    ensure_builtin_skills(tmp_path)
    target = tmp_path / "tools" / "skill_builder" / "SKILL.md"
    before = target.stat().st_mtime_ns
    assert ensure_builtin_skills(tmp_path) == []
    assert target.stat().st_mtime_ns == before


def test_restored_after_being_deleted(tmp_path):
    ensure_builtin_skills(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "tools" / "skill_builder")
    assert ensure_builtin_skills(tmp_path) == ["skill_builder"]
    assert (tmp_path / "tools" / "skill_builder" / "SKILL.md").is_file()


def test_a_hand_edited_builtin_is_left_alone(tmp_path):
    """Restore only what's missing: rewriting existing folders would fight
    anyone who legitimately adjusted the text on disk."""
    ensure_builtin_skills(tmp_path)
    path = tmp_path / "tools" / "skill_builder" / "SKILL.md"
    path.write_text("# my own version\n", encoding="utf-8")
    assert ensure_builtin_skills(tmp_path) == []
    assert path.read_text(encoding="utf-8") == "# my own version\n"


def test_lists_through_the_normal_registry(tmp_path):
    """No special-casing in sync_tools: they're ordinary folders on disk."""
    ensure_builtin_skills(tmp_path)
    manifests = registry.list_tools(tmp_path)
    assert {m.name for m in manifests} == BUILTIN_SKILL_NAMES
    assert all(m.kind == "doc" for m in manifests)


def test_is_builtin_is_name_based():
    assert is_builtin("skill_builder")
    assert not is_builtin("some-admin-skill")


# --------------------------------------------------------------------------
# admin UI: greyed out, no edit/delete
# --------------------------------------------------------------------------


def test_shown_in_the_list_with_a_builtin_badge(admin_client, tmp_path):
    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    html = admin_client.get("/").text
    assert "skill_builder" in html
    assert "is-builtin" in html
    assert "встроенный" in html


def test_no_edit_or_delete_controls_for_a_builtin(admin_client, tmp_path):
    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    html = admin_client.get("/").text
    assert "skill_builder" in html

    # Isolate the built-in's own row and assert on that, rather than on the
    # whole page (other rows legitimately carry these controls).
    import re

    rows = re.findall(r'<div class="skill-row[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    builtin_row = next(r for r in rows if "skill_builder" in r)
    assert "tools/skill_builder/delete" not in builtin_row
    assert "openEditDescription" not in builtin_row
    assert "встроенный" in builtin_row


def test_delete_refused_for_a_builtin(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    r = admin_client.post("/tools/skill_builder/delete")
    assert "встроенный" in r.text.lower()
    assert (shared / "tools" / "skill_builder" / "SKILL.md").is_file()


def test_edit_refused_for_a_builtin(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    r = admin_client.post(
        "/tools/skill_builder/edit-description", data={"description": "hacked", "group": ""}
    )
    assert "встроенный" in r.text.lower()
    assert "hacked" not in (shared / "tools" / "skill_builder" / ".skill_meta.json").read_text()


def test_api_delete_refused_for_a_builtin(admin_client, tmp_path):
    """The JSON API is what a child relay calls, so guarding only the HTML
    route would leave a hole."""
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    r = admin_client.post(
        "/api/tools/skill_builder/delete", auth=(ADMIN_USER, ADMIN_PASSWORD)
    )
    assert r.status_code == 403
    assert "встроенный" in r.json()["error"]


def test_api_edit_refused_for_a_builtin(admin_client, tmp_path):
    shared = tmp_path / "shared"
    admin_client.post("/config/shared-dir", data={"path": str(shared)})
    r = admin_client.post(
        "/api/tools/skill_builder/edit-description",
        auth=(ADMIN_USER, ADMIN_PASSWORD),
        data={"description": "hacked", "group": ""},
    )
    assert r.status_code == 403


def test_ordinary_skills_are_still_deletable(admin_client, tmp_path):
    """The guard must key off the built-in name, not block everything."""
    from jupyter_mcp_tools.common import registry as reg
    from jupyter_mcp_tools.common.config import load_config

    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    shared = load_config().shared_dir_path
    reg.create_doc_skill(shared, name="mine", description="d", body="# mine\n")

    r = admin_client.post("/tools/mine/delete")
    assert "удалён" in r.text
    assert not (shared / "tools" / "mine").exists()


# --------------------------------------------------------------------------
# ordering: built-ins first
# --------------------------------------------------------------------------


def test_builtins_come_first_in_the_page(admin_client, tmp_path):
    """"вверх подними": they're always-present app helpers, so they belong
    in a stable section at the top rather than interleaved with user skills."""
    from jupyter_mcp_tools.common import registry as reg
    from jupyter_mcp_tools.common.config import load_config

    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    shared = load_config().shared_dir_path
    # Alphabetically these sort before "skill_builder", which is exactly the
    # case that would hide the bug.
    for name in ("aaa_skill", "zzz_skill"):
        reg.create_doc_skill(shared, name=name, description="d", body="# x\n")

    html = admin_client.get("/").text
    # Order of appearance of the skill rows themselves, which is what the
    # admin actually scans down.
    row_order = [m for m in re.findall(r'data-search="([^"]*)"', html)]
    names = [r.split()[0] for r in row_order]
    assert names.index("skill_builder") < names.index("aaa_skill")
    assert names.index("skill_builder") < names.index("zzz_skill")


def test_builtins_first_in_the_api_too(admin_client, tmp_path):
    """The relay renders from this endpoint, so ordering must live here
    rather than only in the page."""
    from jupyter_mcp_tools.common import registry as reg
    from jupyter_mcp_tools.common.config import load_config

    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    shared = load_config().shared_dir_path
    reg.create_doc_skill(shared, name="aaa_skill", description="d", body="# x\n")

    names = [t["name"] for t in admin_client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD)).json()]
    assert names[0] == "skill_builder"


def test_api_exposes_the_builtin_flag(admin_client, tmp_path):
    """Regression: the flag was missing from /api/tools, so a child relay
    rendered built-ins as ordinary (editable-looking) skills."""
    admin_client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})
    payload = admin_client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD)).json()
    entry = next(t for t in payload if t["name"] == "skill_builder")
    assert entry["builtin"] is True

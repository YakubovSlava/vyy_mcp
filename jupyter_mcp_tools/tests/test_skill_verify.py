"""Verifying a candidate skill before installing it.

The static validator only parses source. These tests are about the failures
that parse fine and then break at call time — the reason verification
imports the module for real, in a subprocess.
"""
import textwrap

import pytest

from jupyter_mcp_tools.common import skill_verify
from jupyter_mcp_tools.common.skill_verify import (
    verify_code_source,
    verify_doc_bundle,
)

GOOD_SOURCE = '''
def word_count(text: str, unique: bool = False) -> str:
    """Counts words. Use when the user asks how long a text is."""
    return str(len(text.split()))
'''


# --------------------------------------------------------------------------
# code skills
# --------------------------------------------------------------------------


def test_accepts_a_working_skill_and_reports_its_signature():
    result = verify_code_source(GOOD_SOURCE)
    assert result.ok
    assert result.name == "word_count"
    assert "text: str" in result.summary()
    assert "word_count" in result.summary()


def test_rejects_a_module_that_imports_but_cannot_load():
    """The motivating case: this passes the AST validator and only explodes
    when the tool is called, because the skill folder isn't on sys.path."""
    source = textwrap.dedent(
        """
        from lib.util import helper

        def probe(x: str) -> str:
            \"\"\"Does a thing.\"\"\"
            return helper(x)
        """
    )
    result = verify_code_source(source)
    assert not result.ok
    assert "No module named" in result.error
    # The message has to say what to do instead, not just that it failed.
    assert "sys.path" in result.error


def test_rejects_missing_docstring():
    result = verify_code_source("def quiet(x: str) -> str:\n    return x\n")
    assert not result.ok
    assert "docstring" in result.error


def test_rejects_syntax_errors():
    result = verify_code_source("def broken(:\n")
    assert not result.ok
    assert "SyntaxError" in result.error


def test_rejects_an_empty_source():
    assert not verify_code_source("   ").ok


def test_module_level_hang_is_reported_as_a_timeout_not_a_dead_server():
    """Verification must never be able to wedge the MCP server: a skill with
    an infinite loop at import is a timeout, not a hang."""
    source = "while True:\n    pass\n\ndef f(x: str) -> str:\n    \"\"\"D.\"\"\"\n    return x\n"
    result = verify_code_source(source, timeout=3)
    assert not result.ok
    assert "не завершился" in result.error


def test_does_not_execute_the_function_itself():
    """Installing a skill must not trigger its side effects; only the module
    is imported."""
    source = textwrap.dedent(
        """
        SENTINEL = []

        def side_effect() -> str:
            \"\"\"Would be destructive if called.\"\"\"
            SENTINEL.append("called")
            return "done"
        """
    )
    result = verify_code_source(source)
    assert result.ok
    assert result.name == "side_effect"


def test_sibling_file_is_still_reported_as_a_trap():
    """Even with a real data file next to it, importing a sibling *module*
    fails -- so the advice to keep code in one file stays correct."""
    source = 'from helper import h\n\ndef f(x: str) -> str:\n    """D."""\n    return h(x)\n'
    result = verify_code_source(source)
    assert not result.ok
    assert "No module named" in result.error


def test_skill_can_still_import_installed_third_party_packages():
    """Verification runs under the server's own interpreter, so a skill
    using a real dependency must not be rejected."""
    source = 'import json\n\ndef f(x: str) -> str:\n    """Dumps."""\n    return json.dumps({"x": x})\n'
    assert verify_code_source(source).ok


# --------------------------------------------------------------------------
# doc skills
# --------------------------------------------------------------------------


def test_doc_requires_name_description_and_body():
    assert verify_doc_bundle("", name="a", description="b").ok is False
    assert verify_doc_bundle("# body", name="", description="b").ok is False
    assert verify_doc_bundle("# body", name="a", description="   ").ok is False


def test_doc_accepts_a_complete_bundle():
    result = verify_doc_bundle("# Notes\n\nDo the thing.\n", name="notes", description="Use when writing notes.")
    assert result.ok
    assert result.kind == "doc"
    assert "notes" in result.summary()


def test_doc_counts_extra_files():
    result = verify_doc_bundle("# N\n", name="n", description="d", files=["a.md", "b.png"])
    assert result.ok
    assert "дополнительные файлы: 2" in result.summary()

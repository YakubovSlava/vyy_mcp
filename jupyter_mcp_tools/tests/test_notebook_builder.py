import ast
import json

from jupyter_mcp_tools.admin_ui.notebook_builder import build_notebook, build_notebook_json


def test_build_notebook_cells_are_valid_python():
    nb = build_notebook(
        main_host_base_url="https://main.example.org:8766",
        bitbucket_base_url="https://bitbucket.example.org",
    )
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) == 3
    for cell in code_cells:
        ast.parse("".join(cell["source"]))


def test_build_notebook_bakes_in_constants():
    nb = build_notebook(
        main_host_base_url="https://main.example.org:8766",
        bitbucket_base_url="https://bitbucket.example.org",
        bitbucket_project="OPS",
        bitbucket_repo="mcp-config",
        bitbucket_file_path="current_host.txt",
    )
    config_source = "".join(nb["cells"][1]["source"])
    assert "MAIN_HOST = 'https://main.example.org:8766'" in config_source
    assert "BITBUCKET_BASE_URL = 'https://bitbucket.example.org'" in config_source
    assert "BITBUCKET_PROJECT = 'OPS'" in config_source
    assert "BITBUCKET_REPO = 'mcp-config'" in config_source
    assert "BITBUCKET_FILE_PATH = 'current_host.txt'" in config_source


def test_build_notebook_lookup_cell_uses_real_bitbucket_rest_call():
    nb = build_notebook(
        main_host_base_url="https://main.example.org:8766",
        bitbucket_base_url="https://bitbucket.example.org",
    )
    lookup_source = "".join(nb["cells"][2]["source"])
    assert "requests.get(raw_url, auth=(bb_username, bb_password))" in lookup_source
    assert "/rest/api/1.0/projects/" in lookup_source


def test_build_notebook_connect_cell_prints_both_clients():
    nb = build_notebook(
        main_host_base_url="https://main.example.org:8766",
        bitbucket_base_url="https://bitbucket.example.org",
    )
    connect_source = "".join(nb["cells"][3]["source"])
    assert "claude mcp add shared-tools --transport http" in connect_source
    assert "opencode mcp add shared-tools --url" in connect_source
    assert 'X-MCP-User={os_username}' in connect_source


def test_build_notebook_json_is_valid_nbformat():
    text = build_notebook_json(
        main_host_base_url="https://main.example.org:8766",
        bitbucket_base_url="https://bitbucket.example.org",
    )
    nb = json.loads(text)
    assert nb["nbformat"] == 4

import ast
import json

from jupyter_mcp_tools.admin_ui.notebook_builder import build_notebook, build_notebook_json


def test_build_notebook_cells_are_valid_python():
    nb = build_notebook(
        bitbucket_script_path="/opt/scripts/resolve_host.py",
        main_host_base_url="https://main.example.org:8766",
    )
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) == 3
    for cell in code_cells:
        ast.parse("".join(cell["source"]))


def test_build_notebook_bakes_in_constants():
    nb = build_notebook(
        bitbucket_script_path="/opt/scripts/resolve_host.py",
        main_host_base_url="https://main.example.org:8766",
    )
    config_source = "".join(nb["cells"][1]["source"])
    assert "MAIN_HOST = 'https://main.example.org:8766'" in config_source
    assert "BITBUCKET_SCRIPT_PATH = '/opt/scripts/resolve_host.py'" in config_source


def test_build_notebook_json_is_valid_nbformat():
    text = build_notebook_json(
        bitbucket_script_path="/opt/scripts/resolve_host.py",
        main_host_base_url="https://main.example.org:8766",
    )
    nb = json.loads(text)
    assert nb["nbformat"] == 4

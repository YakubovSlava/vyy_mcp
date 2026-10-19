from jupyter_mcp_tools.common.skill_results import SkillFile, files_result


def test_files_result_shape():
    result = files_result(
        "here you go",
        [
            SkillFile(filename="setup.sh", content="echo hi\n", run_hint="bash setup.sh"),
            SkillFile(filename="config.yaml", content="key: value\n"),
        ],
    )

    assert result == {
        "message": "here you go",
        "files": [
            {"filename": "setup.sh", "content": "echo hi\n", "run_hint": "bash setup.sh"},
            {"filename": "config.yaml", "content": "key: value\n", "run_hint": None},
        ],
    }


def test_files_result_is_json_serializable():
    import json

    result = files_result("msg", [SkillFile(filename="a.txt", content="x")])
    assert json.loads(json.dumps(result)) == result

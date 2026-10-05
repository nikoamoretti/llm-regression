from pathlib import Path


from runner.agent import ToolWorkspace, execute_tool


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    workspace = ToolWorkspace(tmp_path)
    result = execute_tool(workspace, "read_file", '{"path": "../secret"}')
    assert result["error"] == "ValueError"


def test_forbidden_path(tmp_path: Path) -> None:
    (tmp_path / "grader").mkdir()
    (tmp_path / "grader" / "hidden.py").write_text("secret", encoding="utf-8")
    workspace = ToolWorkspace(tmp_path, forbidden_paths=["grader/"])
    result = execute_tool(workspace, "read_file", '{"path": "grader/hidden.py"}')
    assert "forbidden" in result["message"].lower()


def test_write_and_read(tmp_path: Path) -> None:
    workspace = ToolWorkspace(tmp_path, writable_paths=["src/"])
    (tmp_path / "src").mkdir()
    execute_tool(workspace, "write_file", '{"path": "src/a.txt", "content": "hi"}')
    read = execute_tool(workspace, "read_file", '{"path": "src/a.txt"}')
    assert read["content"] == "hi"


def test_unknown_command(tmp_path: Path) -> None:
    workspace = ToolWorkspace(tmp_path)
    result = execute_tool(workspace, "run_command", '{"argv": ["curl", "https://example.com"]}')
    assert result["error"] == "ValueError"

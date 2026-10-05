from pathlib import Path

import pytest

from runner.patch import PatchError, extract_unified_diff, git_apply, init_workspace_git


def test_extracts_fenced_diff() -> None:
    text = """Here you go
```diff
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-old
+new
```
"""
    diff = extract_unified_diff(text)
    assert diff.startswith("--- a/src/a.py")


def test_rejects_empty() -> None:
    with pytest.raises(PatchError):
        extract_unified_diff("no patch here")


def test_git_apply(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("old\n", encoding="utf-8")
    init_workspace_git(tmp_path)
    diff = """--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-old
+new
"""
    git_apply(tmp_path, diff)
    assert (tmp_path / "src" / "a.py").read_text(encoding="utf-8") == "new\n"

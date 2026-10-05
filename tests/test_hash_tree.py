from pathlib import Path

from runner.hash_tree import canonical_hash, hash_tree


def test_hash_tree_is_order_independent(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root in (a, b):
        (root / "src").mkdir(parents=True)
        (root / "src" / "one.py").write_text("x = 1\n", encoding="utf-8")
        (root / "src" / "two.py").write_text("x = 2\n", encoding="utf-8")
    assert hash_tree(a) == hash_tree(b)


def test_hash_tree_changes_when_content_changes(tmp_path: Path) -> None:
    root = tmp_path / "t"
    root.mkdir()
    file = root / "f.txt"
    file.write_text("one", encoding="utf-8")
    first = hash_tree(root)
    file.write_text("two", encoding="utf-8")
    assert hash_tree(root) != first


def test_canonical_hash_sorts_keys() -> None:
    assert canonical_hash({"b": 1, "a": 2}) == canonical_hash({"a": 2, "b": 1})

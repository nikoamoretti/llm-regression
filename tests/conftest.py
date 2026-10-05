from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_recorded_images(tmp_path_factory, monkeypatch) -> None:
    """Keep tests independent of images built on this machine (artifacts/images.json).

    Container tests opt back in by pointing LLMREG_IMAGES_FILE at a real file.
    """
    monkeypatch.setenv("LLMREG_IMAGES_FILE", str(tmp_path_factory.mktemp("images") / "images.json"))
    monkeypatch.delenv("LLMREG_ALLOW_HOST", raising=False)

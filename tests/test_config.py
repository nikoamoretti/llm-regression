from pathlib import Path

from runner.config import load_model_config


def test_each_effort_is_its_own_config_hash() -> None:
    hashes = []
    for effort in ("low", "medium", "high", "xhigh", "max"):
        cfg = load_model_config(Path("configs") / f"sol-{effort}.json")
        hashes.append(cfg.config_sha256)
        assert cfg.reasoning_effort == effort
        assert cfg.model == "gpt-5.6-sol"
        assert cfg.track == "codex_product"
        assert cfg.provider == "codex_cli"
    assert len(set(hashes)) == 5


def test_ultra_cannot_use_single_agent_track() -> None:
    from runner.config import model_config_from_json

    cfg = model_config_from_json(
        {
            "provider": "codex_cli",
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "ultra"},
            "track": "codex_product",
        }
    )
    assert cfg.track == "codex_ultra"

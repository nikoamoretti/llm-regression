"""Scientific-data gates. Gold/fake/synthetic runs cannot become baselines."""

from __future__ import annotations

from runner import NON_SCIENTIFIC_SOURCES


def is_scientific_source(source: str | None) -> bool:
    return (source or "model") == "model"


def reject_non_scientific(source: str | None, *, scientific_data: bool | None = None) -> None:
    if source in NON_SCIENTIFIC_SOURCES or scientific_data is False:
        raise ValueError(
            "gold, none, synthetic, and fake runs cannot become a scientific baseline "
            f"(source={source!r}, scientific_data={scientific_data})"
        )

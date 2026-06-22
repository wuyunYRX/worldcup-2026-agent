"""Probability fusion helpers for model and market WDL probabilities."""

from __future__ import annotations

from typing import Optional, Tuple

Triplet = Tuple[float, float, float]


def normalize_triplet(values: Optional[Triplet]) -> Triplet:
    if not values:
        return (0.0, 0.0, 0.0)
    clipped = tuple(max(float(value), 0.0) for value in values[:3])
    total = sum(clipped)
    if total <= 0.0:
        return (0.0, 0.0, 0.0)
    return clipped[0] / total, clipped[1] / total, clipped[2] / total


def fuse_wdl_probabilities(
    model_probabilities: Optional[Triplet],
    market_probabilities: Optional[Triplet],
    model_weight: float = 0.4,
) -> Triplet:
    model = normalize_triplet(model_probabilities)
    market = normalize_triplet(market_probabilities)
    if sum(model) <= 0.0:
        return market
    if sum(market) <= 0.0:
        return model

    weight = max(0.0, min(float(model_weight), 1.0))
    fused = tuple(market[idx] + weight * (model[idx] - market[idx]) for idx in range(3))
    return normalize_triplet(fused)  # type: ignore[arg-type]

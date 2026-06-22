"""Kelly criterion helpers for stake sizing."""

from __future__ import annotations

from typing import List, Tuple


def kelly_fraction(probability: float, odds: float) -> float:
    if odds <= 1.0 or probability <= 0.0:
        return 0.0
    return (probability * odds - 1.0) / (odds - 1.0)


def fractional_kelly(
    probability: float,
    odds: float,
    fraction: float = 0.25,
    min_edge: float = 0.05,
) -> float:
    ev = probability * odds - 1.0
    if ev < min_edge:
        return 0.0
    return max(kelly_fraction(probability, odds), 0.0) * fraction


def kelly_stake(
    probability: float,
    odds: float,
    bankroll: float,
    fraction: float = 0.25,
    min_edge: float = 0.05,
    min_stake: float = 2.0,
    max_stake: float = 5.0,
) -> float:
    stake = fractional_kelly(probability, odds, fraction, min_edge) * bankroll
    if stake < min_stake:
        return 0.0
    return min(stake, max_stake)


def kelly_fractions_for_match(
    probabilities: Tuple[float, float, float],
    odds: List[float],
    fraction: float = 0.25,
    min_edge: float = 0.05,
) -> List[float]:
    return [
        fractional_kelly(probabilities[idx], odds[idx], fraction, min_edge)
        if idx < len(odds) and odds[idx] > 0
        else 0.0
        for idx in range(3)
    ]


def kelly_stakes_for_match(
    probabilities: Tuple[float, float, float],
    odds: List[float],
    bankroll: float,
    fraction: float = 0.25,
    min_edge: float = 0.05,
    min_stake: float = 2.0,
    max_stake: float = 5.0,
) -> List[float]:
    return [
        kelly_stake(probabilities[idx], odds[idx], bankroll, fraction, min_edge, min_stake, max_stake)
        if idx < len(odds) and odds[idx] > 0
        else 0.0
        for idx in range(3)
    ]

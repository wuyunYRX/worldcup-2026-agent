from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _normalize(probabilities: Tuple[float, float, float]) -> Tuple[float, float, float]:
    total = sum(max(value, 0.0) for value in probabilities)
    if total <= 0:
        return 0.34, 0.32, 0.34
    return tuple(max(value, 0.0) / total for value in probabilities)  # type: ignore[return-value]


def _f(prematch: Optional[Dict[str, object]], key: str) -> float:
    if not prematch:
        return 0.0
    value = prematch.get(key, 0)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def adjust_probabilities_with_ai_context(
    probabilities: Optional[Tuple[float, float, float]],
    prematch: Optional[Dict[str, object]],
    risk_config: Dict[str, float],
    market_probabilities: Optional[Tuple[float, float, float]] = None,
) -> Tuple[Optional[Tuple[float, float, float]], Dict[str, object]]:
    if probabilities is None:
        return None, {"enabled": False, "applied": False, "reason": "missing_probabilities"}

    raw_enabled = risk_config.get("enable_ai_probability_adjustment", 1.0)
    if isinstance(raw_enabled, (int, float)):
        enabled = float(raw_enabled) > 0
    else:
        enabled = str(raw_enabled).lower() not in {"0", "0.0", "false", "no", "off"}
    if not enabled:
        return probabilities, {"enabled": False, "applied": False, "reason": "disabled"}
    if not prematch:
        return probabilities, {"enabled": True, "applied": False, "reason": "missing_prematch_context"}

    max_delta = float(risk_config.get("ai_probability_max_delta", 0.03))
    high_conf_max_delta = float(risk_config.get("ai_probability_high_confidence_delta", 0.05))

    home, draw, away = probabilities
    adjusted = [home, draw, away]
    reasons: List[str] = []
    signal_strength = 0.0

    home_injury = _f(prematch, "home_injury_count")
    away_injury = _f(prematch, "away_injury_count")
    home_suspend = _f(prematch, "home_suspension_count")
    away_suspend = _f(prematch, "away_suspension_count")
    home_rotation = _f(prematch, "home_rotation_flag")
    away_rotation = _f(prematch, "away_rotation_flag")
    must_win_home = _f(prematch, "must_win_flag_home")
    must_win_away = _f(prematch, "must_win_flag_away")
    lineup_known = _f(prematch, "home_lineup_known") + _f(prematch, "away_lineup_known")

    if home_injury > away_injury:
        delta = min(max_delta, 0.008 * (home_injury - away_injury))
        adjusted[0] -= delta
        adjusted[1] += delta * 0.6
        adjusted[2] += delta * 0.4
        reasons.append("主队伤停更多")
        signal_strength += delta
    elif away_injury > home_injury:
        delta = min(max_delta, 0.008 * (away_injury - home_injury))
        adjusted[2] -= delta
        adjusted[1] += delta * 0.6
        adjusted[0] += delta * 0.4
        reasons.append("客队伤停更多")
        signal_strength += delta

    if home_suspend > away_suspend:
        delta = min(max_delta, 0.01 * (home_suspend - away_suspend))
        adjusted[0] -= delta
        adjusted[1] += delta * 0.5
        adjusted[2] += delta * 0.5
        reasons.append("主队停赛影响更大")
        signal_strength += delta
    elif away_suspend > home_suspend:
        delta = min(max_delta, 0.01 * (away_suspend - home_suspend))
        adjusted[2] -= delta
        adjusted[1] += delta * 0.5
        adjusted[0] += delta * 0.5
        reasons.append("客队停赛影响更大")
        signal_strength += delta

    if home_rotation >= 1 and away_rotation < 1:
        delta = min(max_delta, 0.02)
        adjusted[0] -= delta
        adjusted[1] += delta * 0.65
        adjusted[2] += delta * 0.35
        reasons.append("主队轮换风险")
        signal_strength += delta
    elif away_rotation >= 1 and home_rotation < 1:
        delta = min(max_delta, 0.02)
        adjusted[2] -= delta
        adjusted[1] += delta * 0.65
        adjusted[0] += delta * 0.35
        reasons.append("客队轮换风险")
        signal_strength += delta
    elif away_rotation >= 1 and home_rotation >= 1:
        delta = min(max_delta, 0.015)
        adjusted[0] -= delta * 0.5
        adjusted[2] -= delta * 0.5
        adjusted[1] += delta
        reasons.append("双方都有轮换风险")
        signal_strength += delta

    if must_win_home >= 1 and must_win_away < 1:
        delta = min(max_delta, 0.018)
        adjusted[0] += delta
        adjusted[1] -= delta * 0.7
        adjusted[2] -= delta * 0.3
        reasons.append("主队战意更强")
        signal_strength += delta
    elif must_win_away >= 1 and must_win_home < 1:
        delta = min(max_delta, 0.018)
        adjusted[2] += delta
        adjusted[1] -= delta * 0.7
        adjusted[0] -= delta * 0.3
        reasons.append("客队战意更强")
        signal_strength += delta

    if market_probabilities and abs(probabilities[0] - market_probabilities[0]) >= 0.12 and lineup_known >= 1:
        reasons.append("赛前情报可用于修正模型与市场分歧")
        signal_strength += 0.005

    if not reasons:
        return probabilities, {"enabled": True, "applied": False, "reason": "no_material_signal"}

    confidence = "high" if lineup_known >= 1 and signal_strength >= 0.045 else "medium" if signal_strength >= 0.02 else "low"
    limit = high_conf_max_delta if confidence == "high" else max_delta
    deltas = [adjusted[idx] - probabilities[idx] for idx in range(3)]
    scale = max(abs(delta) for delta in deltas) / limit if deltas and max(abs(delta) for delta in deltas) > limit else 1.0
    if scale > 1.0:
        adjusted = [probabilities[idx] + deltas[idx] / scale for idx in range(3)]
        deltas = [adjusted[idx] - probabilities[idx] for idx in range(3)]

    normalized = _normalize((adjusted[0], adjusted[1], adjusted[2]))
    final_deltas = [normalized[idx] - probabilities[idx] for idx in range(3)]
    return normalized, {
        "enabled": True,
        "applied": True,
        "base_probabilities": list(probabilities),
        "adjusted_probabilities": list(normalized),
        "delta": final_deltas,
        "reasons": reasons,
        "confidence": confidence,
    }

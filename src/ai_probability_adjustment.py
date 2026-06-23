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
    context_reasons: List[str] = []
    tactical_reasons: List[str] = []
    weather_reasons: List[str] = []
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
    home_pressing = _f(prematch, "home_pressing_level")
    away_pressing = _f(prematch, "away_pressing_level")
    home_line = _f(prematch, "home_defensive_line")
    away_line = _f(prematch, "away_defensive_line")
    home_stability = _f(prematch, "home_tactical_stability")
    away_stability = _f(prematch, "away_tactical_stability")
    mismatch_home = _f(prematch, "tactical_mismatch_home")
    mismatch_away = _f(prematch, "tactical_mismatch_away")
    home_value_ratio = _f(prematch, "home_value_ratio")
    away_value_ratio = _f(prematch, "away_value_ratio")
    home_big5 = _f(prematch, "home_big5_league_players")
    away_big5 = _f(prematch, "away_big5_league_players")
    home_depth = _f(prematch, "home_squad_depth_score")
    away_depth = _f(prematch, "away_squad_depth_score")
    home_absence_loss = _f(prematch, "home_absence_value_loss_eur_m")
    away_absence_loss = _f(prematch, "away_absence_value_loss_eur_m")
    value_mismatch_home = _f(prematch, "player_value_mismatch_home")
    value_mismatch_away = _f(prematch, "player_value_mismatch_away")
    temperature_c = _f(prematch, "temperature_c")
    humidity_pct = _f(prematch, "humidity_pct")
    wind_kph = _f(prematch, "wind_kph")
    precipitation_mm = _f(prematch, "precipitation_mm")
    weather_severity = max(
        _f(prematch, "weather_severity"),
        1.0 if temperature_c >= 35.0 or wind_kph >= 35.0 or precipitation_mm >= 8.0 else 0.0,
        0.7 if temperature_c >= 30.0 or humidity_pct >= 75.0 or wind_kph >= 25.0 or precipitation_mm >= 2.0 else 0.0,
    )
    home_style = str(prematch.get("home_coach_style", "")) if prematch else ""
    away_style = str(prematch.get("away_coach_style", "")) if prematch else ""

    if home_injury > away_injury:
        delta = min(max_delta, 0.008 * (home_injury - away_injury))
        adjusted[0] -= delta
        adjusted[1] += delta * 0.6
        adjusted[2] += delta * 0.4
        reasons.append("主队伤停更多")
        context_reasons.append("主队伤停更多")
        signal_strength += delta
    elif away_injury > home_injury:
        delta = min(max_delta, 0.008 * (away_injury - home_injury))
        adjusted[2] -= delta
        adjusted[1] += delta * 0.6
        adjusted[0] += delta * 0.4
        reasons.append("客队伤停更多")
        context_reasons.append("客队伤停更多")
        signal_strength += delta

    if home_suspend > away_suspend:
        delta = min(max_delta, 0.01 * (home_suspend - away_suspend))
        adjusted[0] -= delta
        adjusted[1] += delta * 0.5
        adjusted[2] += delta * 0.5
        reasons.append("主队停赛影响更大")
        context_reasons.append("主队停赛影响更大")
        signal_strength += delta
    elif away_suspend > home_suspend:
        delta = min(max_delta, 0.01 * (away_suspend - home_suspend))
        adjusted[2] -= delta
        adjusted[1] += delta * 0.5
        adjusted[0] += delta * 0.5
        reasons.append("客队停赛影响更大")
        context_reasons.append("客队停赛影响更大")
        signal_strength += delta

    if home_rotation >= 1 and away_rotation < 1:
        delta = min(max_delta, 0.02)
        adjusted[0] -= delta
        adjusted[1] += delta * 0.65
        adjusted[2] += delta * 0.35
        reasons.append("主队轮换风险")
        context_reasons.append("主队轮换风险")
        signal_strength += delta
    elif away_rotation >= 1 and home_rotation < 1:
        delta = min(max_delta, 0.02)
        adjusted[2] -= delta
        adjusted[1] += delta * 0.65
        adjusted[0] += delta * 0.35
        reasons.append("客队轮换风险")
        context_reasons.append("客队轮换风险")
        signal_strength += delta
    elif away_rotation >= 1 and home_rotation >= 1:
        delta = min(max_delta, 0.015)
        adjusted[0] -= delta * 0.5
        adjusted[2] -= delta * 0.5
        adjusted[1] += delta
        reasons.append("双方都有轮换风险")
        context_reasons.append("双方都有轮换风险")
        signal_strength += delta

    if must_win_home >= 1 and must_win_away < 1:
        delta = min(max_delta, 0.018)
        adjusted[0] += delta
        adjusted[1] -= delta * 0.7
        adjusted[2] -= delta * 0.3
        reasons.append("主队战意更强")
        context_reasons.append("主队战意更强")
        signal_strength += delta
    elif must_win_away >= 1 and must_win_home < 1:
        delta = min(max_delta, 0.018)
        adjusted[2] += delta
        adjusted[1] -= delta * 0.7
        adjusted[0] -= delta * 0.3
        reasons.append("客队战意更强")
        context_reasons.append("客队战意更强")
        signal_strength += delta

    if market_probabilities and abs(probabilities[0] - market_probabilities[0]) >= 0.12 and lineup_known >= 1:
        reasons.append("赛前情报可用于修正模型与市场分歧")
        signal_strength += 0.005

    weather_limit = min(float(risk_config.get("weather_adjustment_max_delta", 0.02)), max_delta)
    weather_weight = float(risk_config.get("weather_adjustment_weight", 1.0))
    if weather_severity > 0 and weather_limit > 0:
        severity_scale = min(max(weather_severity, 0.0), 1.0)
        draw_delta = min(weather_limit, 0.012 * severity_scale * max(weather_weight, 0.0))
        if temperature_c >= 30.0 and humidity_pct >= 70.0:
            draw_delta = min(weather_limit, draw_delta + 0.004 * max(weather_weight, 0.0))
            weather_reasons.append("高温高湿压低比赛节奏")
        elif temperature_c >= 30.0:
            weather_reasons.append("高温增加体能消耗")
        if wind_kph >= 25.0:
            draw_delta = min(weather_limit, draw_delta + 0.004 * max(weather_weight, 0.0))
            weather_reasons.append("强风影响传中和远射稳定性")
        if precipitation_mm >= 2.0:
            draw_delta = min(weather_limit, draw_delta + 0.004 * max(weather_weight, 0.0))
            weather_reasons.append("降雨增加控球和防守失误波动")
        if draw_delta > 0:
            adjusted[1] += draw_delta
            adjusted[0] -= draw_delta * 0.5
            adjusted[2] -= draw_delta * 0.5
            if not weather_reasons:
                weather_reasons.append("天气条件偏不利进攻发挥")
            reasons.append("天气因素提高小比分/平局权重")
            context_reasons.extend(weather_reasons[:2])
            signal_strength += draw_delta

    context_adjusted = _normalize((adjusted[0], adjusted[1], adjusted[2]))
    adjusted = list(context_adjusted)

    if home_style == "possession" and away_style == "low_block":
        delta = min(max_delta, 0.012)
        adjusted[1] += delta
        adjusted[0] -= delta * 0.6
        adjusted[2] -= delta * 0.4
        reasons.append("主队控球遇客队低位防守")
        tactical_reasons.append("主队控球遇客队低位防守")
        signal_strength += delta

    if away_style == "counter" and home_line >= 0.62:
        delta = min(max_delta, 0.014)
        adjusted[2] += delta
        adjusted[0] -= delta * 0.6
        adjusted[1] -= delta * 0.4
        reasons.append("主队高防线遇客队反击")
        tactical_reasons.append("主队高防线遇客队反击")
        signal_strength += delta

    if home_style == "counter" and away_line >= 0.62:
        delta = min(max_delta, 0.014)
        adjusted[0] += delta
        adjusted[2] -= delta * 0.6
        adjusted[1] -= delta * 0.4
        reasons.append("客队高防线遇主队反击")
        tactical_reasons.append("客队高防线遇主队反击")
        signal_strength += delta

    if home_pressing >= 0.65 and away_stability <= 0.45:
        delta = min(max_delta, 0.012)
        adjusted[0] += delta
        adjusted[1] -= delta * 0.5
        adjusted[2] -= delta * 0.5
        reasons.append("主队高压针对客队体系不稳")
        tactical_reasons.append("主队高压针对客队体系不稳")
        signal_strength += delta

    if away_pressing >= 0.65 and home_stability <= 0.45:
        delta = min(max_delta, 0.012)
        adjusted[2] += delta
        adjusted[1] -= delta * 0.5
        adjusted[0] -= delta * 0.5
        reasons.append("客队高压针对主队体系不稳")
        tactical_reasons.append("客队高压针对主队体系不稳")
        signal_strength += delta

    if mismatch_home > 0:
        delta = min(max_delta, 0.015 * mismatch_home)
        adjusted[0] += delta
        adjusted[1] -= delta * 0.6
        adjusted[2] -= delta * 0.4
        reasons.append("主队战术克制更有利")
        tactical_reasons.append("主队战术克制更有利")
        signal_strength += delta
    if mismatch_away > 0:
        delta = min(max_delta, 0.015 * mismatch_away)
        adjusted[2] += delta
        adjusted[1] -= delta * 0.6
        adjusted[0] -= delta * 0.4
        reasons.append("客队战术克制更有利")
        tactical_reasons.append("客队战术克制更有利")
        signal_strength += delta

    tactical_adjusted = _normalize((adjusted[0], adjusted[1], adjusted[2]))
    adjusted = list(tactical_adjusted)

    value_reasons: List[str] = []
    value_enabled = (home_value_ratio >= 2.0 or away_value_ratio >= 2.0 or abs(home_big5 - away_big5) >= 5 or abs(home_depth - away_depth) >= 0.25 or home_absence_loss >= 15.0 or away_absence_loss >= 15.0)
    value_adjusted = list(adjusted)
    value_limit = min(float(risk_config.get("value_adjustment_max_delta", 0.025)), max_delta)
    value_weight = float(risk_config.get("value_adjustment_weight", 1.0))
    if value_enabled:
        if home_value_ratio >= 2.0:
            delta = min(value_limit, 0.012 * value_weight)
            value_adjusted[0] += delta
            value_adjusted[1] -= delta * 0.55
            value_adjusted[2] -= delta * 0.45
            value_reasons.append("主队总身价显著占优")
        if away_value_ratio >= 2.0:
            delta = min(value_limit, 0.012 * value_weight)
            value_adjusted[2] += delta
            value_adjusted[1] -= delta * 0.55
            value_adjusted[0] -= delta * 0.45
            value_reasons.append("客队总身价显著占优")
        if home_big5 - away_big5 >= 5 or home_depth - away_depth >= 0.25:
            delta = min(value_limit, 0.008 * value_weight)
            value_adjusted[0] += delta
            value_adjusted[1] -= delta * 0.5
            value_adjusted[2] -= delta * 0.5
            value_reasons.append("主队阵容深度与五大联赛经验占优")
        if away_big5 - home_big5 >= 5 or away_depth - home_depth >= 0.25:
            delta = min(value_limit, 0.008 * value_weight)
            value_adjusted[2] += delta
            value_adjusted[1] -= delta * 0.5
            value_adjusted[0] -= delta * 0.5
            value_reasons.append("客队阵容深度与五大联赛经验占优")
        if home_absence_loss >= 15.0:
            delta = min(value_limit, 0.01 * value_weight)
            value_adjusted[0] -= delta
            value_adjusted[1] += delta * 0.5
            value_adjusted[2] += delta * 0.5
            value_reasons.append("主队核心缺阵价值损失较大")
        if away_absence_loss >= 15.0:
            delta = min(value_limit, 0.01 * value_weight)
            value_adjusted[2] -= delta
            value_adjusted[1] += delta * 0.5
            value_adjusted[0] += delta * 0.5
            value_reasons.append("客队核心缺阵价值损失较大")
        if value_mismatch_home > 0.15:
            delta = min(value_limit, 0.008 * value_weight)
            value_adjusted[0] += delta
            value_adjusted[1] -= delta * 0.5
            value_adjusted[2] -= delta * 0.5
            value_reasons.append("主队球员能力体系更完整")
        if value_mismatch_away > 0.15:
            delta = min(value_limit, 0.008 * value_weight)
            value_adjusted[2] += delta
            value_adjusted[1] -= delta * 0.5
            value_adjusted[0] -= delta * 0.5
            value_reasons.append("客队球员能力体系更完整")
        adjusted = value_adjusted

    if not reasons and not value_reasons:
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
        "context_adjusted_probabilities": list(context_adjusted),
        "weather_adjusted_probabilities": list(context_adjusted),
        "tactical_adjusted_probabilities": list(tactical_adjusted),
        "value_adjusted_probabilities": list(normalized),
        "adjusted_probabilities": list(normalized),
        "delta": final_deltas,
        "reasons": reasons,
        "context_reasons": context_reasons,
        "tactical_reasons": tactical_reasons,
        "weather_reasons": weather_reasons,
        "applied_weather": bool(weather_reasons),
        "applied_tactical": bool(tactical_reasons),
        "value_reasons": value_reasons,
        "applied_value": bool(value_reasons),
        "confidence": confidence,
    }

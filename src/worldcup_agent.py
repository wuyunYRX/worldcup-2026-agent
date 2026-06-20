#!/usr/bin/env python3
"""Generate a 2026 World Cup prediction and lottery EV HTML report.

The report renderer uses Playwright to create a PNG screenshot for Telegram.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import parse, request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODDS_URL = "https://cp.zgzcw.com/lottery/jchtplayvsForJsp.action?lotteryId=47&type=jcmini"
OUTCOME_NAMES = ["主胜", "平", "客胜"]


def normalize_text(value: str) -> str:
    text = html.unescape(value).strip()
    return re.sub(r"[\uE000-\uF8FF\uFFFD]", "", text).strip()


def decode_page_bytes(data: bytes) -> str:
    for enc, errors in (("utf-8", "strict"), ("gb18030", "ignore"), ("gbk", "ignore")):
        try:
            text = data.decode(enc, errors=errors)
        except UnicodeDecodeError:
            continue
        if any(token in text for token in ("比赛时间", "spArr", 'class="beginBet')):
            return text
        normalized = normalize_text(text)
        if any(token in normalized for token in ("比赛时间", "世界杯", 'class="beginBet')):
            return normalized
        return text
    return data.decode("utf-8", errors="ignore")


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def fetch_text(url: str, timeout: int = 25) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 WorldCupAgent/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return decode_page_bytes(data)


def load_model(path: Path) -> Dict[Tuple[str, str], Tuple[float, float, float]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    model: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    for key, values in raw.items():
        home, away = key.split("|", 1)
        if len(values) != 3:
            raise ValueError(f"Model row must have 3 probabilities: {key}")
        total = sum(values)
        if not 0.98 <= total <= 1.02:
            raise ValueError(f"Model probabilities must sum near 1: {key}={values}")
        model[(home, away)] = (float(values[0]), float(values[1]), float(values[2]))
    return model


def parse_attrs(tag: str) -> Dict[str, str]:
    return {k.lower(): normalize_text(v) for k, v in re.findall(r'(\w+)="([^"]*)"', tag)}


def parse_float_triplet(part: str) -> List[float]:
    nums = []
    for item in part.split():
        try:
            nums.append(float(item))
        except ValueError:
            nums.append(0.0)
    while len(nums) < 3:
        nums.append(0.0)
    return nums[:3]


def poisson_probs(lam: float, max_goals: int = 7) -> List[float]:
    return [math.exp(-lam) * (lam ** goals) / math.factorial(goals) for goals in range(max_goals + 1)]


def score_grid(home_lam: float, away_lam: float, max_goals: int = 7) -> List[Tuple[int, int, float]]:
    home_probs = poisson_probs(home_lam, max_goals)
    away_probs = poisson_probs(away_lam, max_goals)
    grid = [(h, a, home_probs[h] * away_probs[a]) for h in range(max_goals + 1) for a in range(max_goals + 1)]
    total = sum(prob for _, _, prob in grid)
    return [(h, a, prob / total) for h, a, prob in grid]


def score_prediction_from_wdl(probabilities: Tuple[float, float, float]) -> dict:
    """Infer a simple Poisson score model from W/D/L probabilities.

    This is a transparent approximation, not a calibrated score market. It is
    sufficient for ranking likely scores and checking whether a W/D/L view
    implies a low- or high-scoring game.
    """

    best_error = float("inf")
    best_lambdas = (1.35, 1.10)
    # Search realistic international football scoring ranges.
    values = [round(0.25 + step * 0.05, 2) for step in range(76)]
    for home_lam in values:
        for away_lam in values:
            home_win = draw = away_win = 0.0
            for home_goals, away_goals, prob in score_grid(home_lam, away_lam, max_goals=6):
                if home_goals > away_goals:
                    home_win += prob
                elif home_goals == away_goals:
                    draw += prob
                else:
                    away_win += prob
            error = (
                (home_win - probabilities[0]) ** 2
                + (draw - probabilities[1]) ** 2
                + (away_win - probabilities[2]) ** 2
            )
            if error < best_error:
                best_error = error
                best_lambdas = (home_lam, away_lam)

    grid = score_grid(best_lambdas[0], best_lambdas[1], max_goals=7)
    top_scores = sorted(grid, key=lambda item: item[2], reverse=True)[:3]
    over_25 = sum(prob for home_goals, away_goals, prob in grid if home_goals + away_goals >= 3)
    both_score = sum(prob for home_goals, away_goals, prob in grid if home_goals > 0 and away_goals > 0)
    return {
        "lambda_home": best_lambdas[0],
        "lambda_away": best_lambdas[1],
        "top_scores": top_scores,
        "over_25": over_25,
        "both_score": both_score,
    }


def score_text(prediction: dict) -> str:
    return " / ".join(f"{home}-{away} ({pct(prob, 1)})" for home, away, prob in prediction["top_scores"])


def simplify_team_name(name: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", normalize_text(name))


def common_prefix_len(a: str, b: str) -> int:
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def build_team_aliases(model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> Dict[str, str]:
    teams = sorted({team for pair in model for team in pair})
    aliases: Dict[str, str] = {}
    for team in teams:
        aliases[simplify_team_name(team)] = team
    return aliases


def resolve_team_name(name: str, aliases: Dict[str, str]) -> str:
    simplified = simplify_team_name(name)
    if not simplified:
        return name
    exact = aliases.get(simplified)
    if exact:
        return exact

    best_name = name
    best_score = 0
    for alias_key, canonical in aliases.items():
        score = max(common_prefix_len(simplified, alias_key), common_prefix_len(alias_key, simplified))
        if score > best_score:
            best_score = score
            best_name = canonical
    if best_score >= 2:
        return best_name
    return name


def infer_team_strengths(model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> Dict[str, float]:
    strengths: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for (home, away), (home_win, _draw, away_win) in model.items():
        edge = home_win - away_win
        strengths[home] = strengths.get(home, 0.0) + edge
        strengths[away] = strengths.get(away, 0.0) - edge
        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
    for team, total in list(strengths.items()):
        strengths[team] = total / max(counts.get(team, 1), 1)
    return strengths


def estimate_match_probabilities(home: str, away: str, strengths: Dict[str, float]) -> Optional[Tuple[float, float, float]]:
    if home not in strengths or away not in strengths:
        return None

    delta = strengths[home] - strengths[away] + 0.08
    draw = max(0.18, min(0.30, 0.26 - abs(delta) * 0.12))
    remaining = 1.0 - draw
    home_share = 1.0 / (1.0 + math.exp(-delta * 3.2))
    home_win = remaining * home_share
    away_win = remaining - home_win
    return (home_win, draw, away_win)


def parse_odds_page(page: str, model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> List[dict]:
    aliases = build_team_aliases(model)
    strengths = infer_team_strengths(model)
    blocks = re.findall(r'(<tr\b(?=[^>]*\bm="[^"]+")[\s\S]*?</tr>)', page, flags=re.I)
    rows: List[dict] = []
    for block in blocks:
        tag = block.split(">", 1)[0]
        attrs = parse_attrs(tag)
        titles = [normalize_text(x) for x in re.findall(r'<a[^>]+title="([^"]+)"[^>]*>', block)]
        if len(titles) < 2:
            continue
        raw_home, raw_away = titles[-2:]
        home = resolve_team_name(raw_home, aliases)
        away = resolve_team_name(raw_away, aliases)
        probs = model.get((home, away))
        if probs is None:
            probs = estimate_match_probabilities(home, away, strengths)

        match_time = ""
        match_time_match = re.search(r'title="比赛时间:([^"]+)"', block)
        if match_time_match:
            match_time = normalize_text(match_time_match.group(1))

        sp_arr_match = re.search(r'class="spArr"[\s\S]*?value="([^"]*)"', block)
        sp_value = html.unescape(sp_arr_match.group(1)) if sp_arr_match else ""
        parts = sp_value.split("|") if sp_value else []
        normal_odds = parse_float_triplet(parts[0]) if parts else [0.0, 0.0, 0.0]
        handicap_odds = parse_float_triplet(parts[1]) if len(parts) > 1 else [0.0, 0.0, 0.0]
        ev = [
            probs[i] * normal_odds[i] - 1 if probs and normal_odds[i] > 0 else None
            for i in range(3)
        ]

        rows.append(
            {
                "num": attrs.get("mn", ""),
                "home": home,
                "away": away,
                "match_time": match_time,
                "deadline": attrs.get("t", ""),
                "handicap": attrs.get("rq", ""),
                "single": attrs.get("dg", ""),
                "probabilities": probs,
                "normal_odds": normal_odds,
                "handicap_odds": handicap_odds,
                "ev": ev,
                "score_prediction": score_prediction_from_wdl(probs) if probs else None,
            }
        )
    return sorted(rows, key=lambda r: (r["match_time"], r["num"]))


def probability_text(probabilities: Optional[Tuple[float, float, float]]) -> str:
    if not probabilities:
        return "待补模型"
    return " / ".join(pct(x) for x in probabilities)


def score_prediction_text(prediction: Optional[dict]) -> str:
    if not prediction:
        return "待补模型"
    return score_text(prediction)


def pct(value: float, digits: int = 0) -> str:
    return f"{value * 100:.{digits}f}%"


def odds_text(odds: Iterable[float]) -> str:
    values = list(odds)
    if not values or all(v <= 0 for v in values):
        return "普通胜平负未开"
    return " / ".join(f"{v:.2f}" for v in values)


def ev_text(value: Optional[float]) -> str:
    if value is None:
        return "只开让球盘"
    return f"{value * 100:+.1f}%"


def ev_class(value: Optional[float]) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "pos"
    if value > -0.02:
        return "near"
    return "neg"


def best_ev_remark(row: dict) -> str:
    ev = row["ev"]
    odds = row["normal_odds"]
    if not row["probabilities"]:
        if all(x <= 0 for x in odds):
            return f"已抓到实时赛程；普通胜平负未开，当前让球 {row['handicap']}，让球赔率 {odds_text(row['handicap_odds'])}。"
        return "已抓到实时赔率，但当前模型未覆盖此对阵，暂不计算 EV。"
    if all(x <= 0 for x in odds):
        return f"普通胜平负未开；当前让球 {row['handicap']}，让球赔率 {odds_text(row['handicap_odds'])}。"
    best_idx = max(range(3), key=lambda i: ev[i] if ev[i] is not None else -999)
    best = ev[best_idx]
    if best is not None and best > 0:
        return f"最佳 EV：{OUTCOME_NAMES[best_idx]} {ev_text(best)}。"
    if best is not None and best > -0.02:
        return f"接近保本：{OUTCOME_NAMES[best_idx]} {ev_text(best)}。"
    return "热门方向赔率被压低，暂不作主推。"


def select_candidates(rows: List[dict]) -> List[Tuple[float, dict, int]]:
    candidates: List[Tuple[float, dict, int]] = []
    for row in rows:
        for idx, value in enumerate(row["ev"]):
            if value is not None and value > -0.02:
                candidates.append((value, row, idx))
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def select_ticket(rows: List[dict]) -> List[Tuple[dict, int]]:
    """Pick a conservative positive-EV single-ticket basket.

    The rule is intentionally simple and auditable:
    - include positive EV items with probability >= 24%;
    - always cap at 5 selections;
    - avoid adding many low-probability long shots.
    """

    picks: List[Tuple[dict, int]] = []
    for value, row, idx in select_candidates(rows):
        probability = row["probabilities"][idx]
        if value > 0 and probability >= 0.24:
            picks.append((row, idx))
        if len(picks) >= 5:
            break
    return picks


def ticket_metrics(picks: List[Tuple[dict, int]], stake_per_pick: float = 2.0) -> Tuple[float, float, float, float]:
    if not picks:
        return 0.0, 0.0, 0.0, 0.0
    total_stake = stake_per_pick * len(picks)
    expected_return = sum(row["probabilities"][idx] * row["normal_odds"][idx] * stake_per_pick for row, idx in picks)

    grouped: Dict[Tuple[str, str, str], dict] = {}
    for row, idx in picks:
        key = (row["num"], row["home"], row["away"])
        grouped.setdefault(key, {"row": row, "indices": []})["indices"].append(idx)
    groups = list(grouped.values())

    profit_probability = 0.0

    def walk(position: int, probability: float, odds_sum: float) -> None:
        nonlocal profit_probability
        if position == len(groups):
            if odds_sum * stake_per_pick > total_stake:
                profit_probability += probability
            return
        group = groups[position]
        row = group["row"]
        selected = set(group["indices"])
        for outcome_idx, outcome_probability in enumerate(row["probabilities"]):
            add = row["normal_odds"][outcome_idx] if outcome_idx in selected else 0.0
            walk(position + 1, probability * outcome_probability, odds_sum + add)

    walk(0, 1.0, 0.0)
    roi = expected_return / total_stake - 1
    return total_stake, expected_return, roi, profit_probability


def row_html(row: dict) -> str:
    probs = row["probabilities"]
    evs = row["ev"]
    probability_cell = probability_text(probs)
    ev_cells = " / ".join(f'<span class="{ev_class(x)}">{ev_text(x)}</span>' for x in evs)
    return (
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{html.escape(probability_cell)}</td>"
        f"<td>{html.escape(score_prediction_text(row['score_prediction']))}</td>"
        f"<td>{html.escape(odds_text(row['normal_odds']))}</td>"
        f"<td>{ev_cells}</td>"
        f"<td>{html.escape(best_ev_remark(row))}</td>"
        "</tr>"
    )


def generate_report(rows: List[dict], odds_url: str, generated_at: dt.datetime) -> str:
    candidates = select_candidates(rows)
    picks = select_ticket(rows)
    stake, expected_return, roi, profit_probability = ticket_metrics(picks)
    modeled_rows = [row for row in rows if row["probabilities"]]

    focus = rows[:2]
    focus_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{html.escape(probability_text(row['probabilities']))}</td>"
        f"<td>{html.escape(score_prediction_text(row['score_prediction']))}</td>"
        f"<td>{html.escape(odds_text(row['normal_odds']))}</td>"
        f"<td>{html.escape(best_ev_remark(row))}</td>"
        "</tr>"
        for row in focus
    )

    candidate_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{OUTCOME_NAMES[idx]}</td>"
        f"<td>{pct(row['probabilities'][idx], 1)}</td>"
        f"<td>{row['normal_odds'][idx]:.2f}</td>"
        f"<td>{1 / row['probabilities'][idx]:.2f}</td>"
        f"<td class=\"{ev_class(value)}\">{ev_text(value)}</td>"
        "</tr>"
        for value, row, idx in candidates[:12]
    )

    ticket_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{OUTCOME_NAMES[idx]}</td>"
        f"<td>{pct(row['probabilities'][idx], 1)}</td>"
        f"<td>{row['normal_odds'][idx]:.2f}</td>"
        f"<td>{ev_text(row['ev'][idx])}</td>"
        "</tr>"
        for row, idx in picks
    )
    if not ticket_rows:
        ticket_rows = '<tr><td colspan="6">当前没有满足筛选条件的正 EV 主票，建议跳过。</td></tr>'

    no_normal = [
        f"{row['home']} vs {row['away']}"
        for row in rows
        if all(x <= 0 for x in row["normal_odds"])
    ]
    no_normal_text = "、".join(no_normal) if no_normal else "无"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>世界杯每日预测与购彩报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; line-height: 1.55; color: #172033; background: #f6f8fb; margin: 0; padding: 24px; }}
main {{ max-width: 1180px; margin: 0 auto; background: #fff; padding: 28px; border: 1px solid #d9e1ec; }}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
h2 {{ margin: 24px 0 10px; font-size: 19px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 13px; }}
th, td {{ border: 1px solid #dfe6ef; padding: 8px; vertical-align: top; }}
th {{ background: #eef3f8; text-align: left; }}
.stamp, .muted {{ color: #617086; }}
.notice {{ background: #fff8df; border-left: 4px solid #d99a00; padding: 10px 12px; }}
.ticket {{ background: #f2fff7; border-left: 4px solid #1b9c66; padding: 10px 12px; }}
.pos {{ color: #087f4f; font-weight: 700; }}
.near {{ color: #9a6500; font-weight: 700; }}
.neg {{ color: #8a2432; }}
</style>
</head>
<body>
<main>
<h1>世界杯每日预测与购彩报告</h1>
<p class="stamp">生成时间：{generated_at.strftime("%Y-%m-%d %H:%M:%S")}（本机时间）</p>
<p class="notice">所有概率均为模型主观预测，不是保证命中，也不是官方赔率。购彩建议只用于控制风险和比较赔率价值，请控制预算、少串关，不要把预测当确定收益。</p>

<section id="summary">
<h2>本轮摘要</h2>
<p>当前抓取到可售世界杯场次 {len(rows)} 场，其中模型已覆盖 {len(modeled_rows)} 场。普通胜平负未开场次：{html.escape(no_normal_text)}。</p>
</section>

<section id="matches-24h">
<h2>今日/未来 24 小时重点比赛</h2>
<table><thead><tr><th>开赛时间</th><th>场次</th><th>模型胜平负概率</th><th>最可能比分</th><th>当前不让球赔率</th><th>判断</th></tr></thead><tbody>
{focus_rows}
</tbody></table>
</section>

<section id="group-update">
<h2>小组名次和最佳第三名变化</h2>
<p>当前脚本版本保留该区块用于接入官方赛果和积分数据。若未接入实时积分源，请以 FIFA 官方赛程赛果为准。</p>
</section>

<section id="live-postmatch">
<h2>赛前/实时/赛后更新</h2>
<p>当前脚本主要完成赔率、概率和 EV 自动化；首发、伤停、天气和实时赛况建议在赛前 60-90 分钟接入可靠数据源后刷新。</p>
</section>

<section id="remaining-probabilities">
<h2>当前剩余可售世界杯场次</h2>
<table><thead><tr><th>比赛编号</th><th>开赛时间</th><th>场次</th><th>模型胜平负概率（主胜/平/客胜）</th><th>最可能比分</th><th>当前不让球赔率（主胜/平/客胜）</th><th>EV（主胜/平/客胜）</th><th>备注</th></tr></thead><tbody>
{''.join(row_html(row) for row in rows)}
</tbody></table>
</section>

<section id="ev-board">
<h2>赔率与 EV 筛选</h2>
<p>EV = 模型概率 × 当前赔率 - 1。正 EV 代表按模型估计有赔率价值，但不代表一定赚钱。</p>
<table><thead><tr><th>编号</th><th>场次</th><th>选项</th><th>模型命中率</th><th>赔率</th><th>保本赔率</th><th>EV</th></tr></thead><tbody>
{candidate_rows or '<tr><td colspan="7">当前没有正 EV 或接近正 EV 的普通胜平负选项。</td></tr>'}
</tbody></table>
</section>

<section id="ticket">
<h2>最终建议票单</h2>
<div class="ticket">
<p><b>主建议：单关/分散买，少串关。</b> 预算示例：每个选项 2 元，共 {len(picks)} 注，合计 {stake:.2f} 元；模型期望返还约 {expected_return:.2f} 元，ROI 约 {roi * 100:.1f}%；整组最终赚钱概率约 {profit_probability * 100:.1f}%。</p>
</div>
<table><thead><tr><th>编号</th><th>场次</th><th>购买选项</th><th>单项命中率</th><th>赔率</th><th>EV</th></tr></thead><tbody>
{ticket_rows}
</tbody></table>
</section>

<section id="changes">
<h2>与上一版变化</h2>
<p>当前开源版本未保存历史快照。生产使用可将每次 odds snapshot 和 report metadata 落盘后比较赔率、EV 和推荐票单变化。</p>
</section>

<section id="risks">
<h2>关键风险因素</h2>
<ul>
<li>临场首发、伤停、红黄牌和天气变化会影响概率。</li>
<li>赔率临近停售会变化，下单前必须重新计算。</li>
<li>部分强弱悬殊场次只开让球盘，本报告不会在缺少净胜球分布时强推让球。</li>
<li>模型概率需要持续校准；示例概率不应直接视为长期稳定优势。</li>
</ul>
</section>

<section id="sources">
<h2>来源</h2>
<ul>
<li><a href="{html.escape(odds_url)}">足彩网竞彩足球胜平负/让球赔率页</a></li>
<li><a href="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures">FIFA 2026 World Cup scores and fixtures</a></li>
</ul>
</section>
</main>
</body>
</html>
"""


def render_report_screenshot(report_path: Path, screenshot_path: Path) -> bool:
    """Render the HTML report to a full-page PNG screenshot.

    Playwright is imported lazily so the analysis/report generation still works
    even if a user has not installed screenshot support yet.
    """

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"Screenshot skipped: Playwright is not available ({exc}).", file=sys.stderr)
        return False

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1800, "height": 2200}, device_scale_factor=2)
        page.goto(report_path.resolve().as_uri(), wait_until="networkidle")
        page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()
    return True


def multipart_body(fields: Dict[str, str], file_field: str, file_path: Path) -> Tuple[bytes, str]:
    boundary = f"----WorldCupAgent{uuid.uuid4().hex}"
    chunks: List[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def send_telegram_screenshot(env: Dict[str, str], screenshot_path: Path, generated_at: dt.datetime) -> None:
    if env.get("SEND_TELEGRAM", "0") not in {"1", "true", "TRUE", "yes"}:
        return
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.", file=sys.stderr)
        return
    if not screenshot_path.exists():
        print(f"Telegram skipped: screenshot does not exist: {screenshot_path}", file=sys.stderr)
        return

    caption = (
        "世界杯每日预测更新\n"
        f"生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "完整高清报告截图见附件。"
    )
    payload, boundary = multipart_body(
        {
            "chat_id": chat_id,
            "caption": caption,
            "disable_web_page_preview": "true",
        },
        "document",
        screenshot_path,
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"

    def post(opener: request.OpenerDirector) -> None:
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with opener.open(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error: {body}")

    try:
        post(request.build_opener())
    except Exception as first_error:
        proxy = env.get("TELEGRAM_PROXY", "")
        if not proxy:
            raise
        try:
            post(request.build_opener(request.ProxyHandler({"http": proxy, "https": proxy})))
        except Exception as second_error:
            print(f"Telegram send failed: {type(second_error).__name__}: {second_error}", file=sys.stderr)
            print(f"First attempt was: {type(first_error).__name__}: {first_error}", file=sys.stderr)


def resolve_report_url(report_path: Path, env: Dict[str, str]) -> str:
    configured = env.get("REPORT_URL", "").strip()
    if not configured or "absolute/path/to" in configured:
        return report_path.resolve().as_uri()
    return configured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--odds-html", help="Use a local odds HTML file instead of fetching")
    args = parser.parse_args()

    env = {**os.environ, **load_env(ROOT / args.env)}
    odds_url = env.get("ODDS_URL", DEFAULT_ODDS_URL)
    report_path = ROOT / env.get("REPORT_PATH", "docs/worldcup-2026-agent-report.html")
    screenshot_path = ROOT / env.get("REPORT_SCREENSHOT_PATH", "docs/worldcup-2026-agent-report.png")
    report_url = resolve_report_url(report_path, env)

    model = load_model(ROOT / "config" / "model_probabilities.json")
    if args.odds_html:
        page = decode_page_bytes(Path(args.odds_html).read_bytes())
    else:
        page = fetch_text(odds_url)

    rows = parse_odds_page(page, model)
    if not rows:
        print("No World Cup odds rows were parsed.", file=sys.stderr)

    generated_at = dt.datetime.now()
    report = generate_report(rows, odds_url, generated_at)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    screenshot_ok = render_report_screenshot(report_path, screenshot_path)
    send_telegram_screenshot(env, screenshot_path, generated_at)
    print(f"Report written: {report_path}")
    if screenshot_ok:
        print(f"Screenshot written: {screenshot_path}")
    print(f"Report URL: {report_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate a 2026 World Cup prediction and lottery EV HTML report.

This script intentionally uses only the Python standard library so the project
is easy to install on a clean machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import parse, request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODDS_URL = "https://cp.zgzcw.com/lottery/jchtplayvsForJsp.action?lotteryId=47&type=jcmini"
OUTCOME_NAMES = ["主胜", "平", "客胜"]


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
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


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
    return {k.lower(): html.unescape(v) for k, v in re.findall(r'(\w+)="([^"]*)"', tag)}


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


def parse_odds_page(page: str, model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> List[dict]:
    blocks = re.findall(r'(<tr\b(?=[^>]*\bm="世界杯")[\s\S]*?</tr>)', page, flags=re.I)
    rows: List[dict] = []
    for block in blocks:
        tag = block.split(">", 1)[0]
        attrs = parse_attrs(tag)
        titles = [
            html.unescape(x)
            for x in re.findall(r'<a[^>]+title="([^"]+)"[^>]*>', block)
            if x != "世界杯"
        ]
        if len(titles) < 2:
            continue
        home, away = titles[:2]
        probs = model.get((home, away))
        if not probs:
            continue

        match_time = ""
        match_time_match = re.search(r'title="比赛时间:([^"]+)"', block)
        if match_time_match:
            match_time = html.unescape(match_time_match.group(1))

        sp_arr_match = re.search(r'class="spArr"[\s\S]*?value="([^"]*)"', block)
        sp_value = html.unescape(sp_arr_match.group(1)) if sp_arr_match else ""
        parts = sp_value.split("|") if sp_value else []
        normal_odds = parse_float_triplet(parts[0]) if parts else [0.0, 0.0, 0.0]
        handicap_odds = parse_float_triplet(parts[1]) if len(parts) > 1 else [0.0, 0.0, 0.0]
        ev = [
            probs[i] * normal_odds[i] - 1 if normal_odds[i] > 0 else None
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
            }
        )
    return sorted(rows, key=lambda r: (r["match_time"], r["num"]))


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
    probability_text = " / ".join(pct(x) for x in probs)
    ev_cells = " / ".join(f'<span class="{ev_class(x)}">{ev_text(x)}</span>' for x in evs)
    return (
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{probability_text}</td>"
        f"<td>{html.escape(odds_text(row['normal_odds']))}</td>"
        f"<td>{ev_cells}</td>"
        f"<td>{html.escape(best_ev_remark(row))}</td>"
        "</tr>"
    )


def generate_report(rows: List[dict], odds_url: str, generated_at: dt.datetime) -> str:
    candidates = select_candidates(rows)
    picks = select_ticket(rows)
    stake, expected_return, roi, profit_probability = ticket_metrics(picks)

    focus = rows[:2]
    focus_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{' / '.join(pct(x) for x in row['probabilities'])}</td>"
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
<p>当前抓取到可售且模型覆盖的世界杯场次 {len(rows)} 场。普通胜平负未开场次：{html.escape(no_normal_text)}。</p>
</section>

<section id="matches-24h">
<h2>今日/未来 24 小时重点比赛</h2>
<table><thead><tr><th>开赛时间</th><th>场次</th><th>模型胜平负概率</th><th>当前不让球赔率</th><th>判断</th></tr></thead><tbody>
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
<h2>当前剩余可售世界杯场次模型胜平负概率</h2>
<table><thead><tr><th>比赛编号</th><th>开赛时间</th><th>场次</th><th>模型胜平负概率（主胜/平/客胜）</th><th>当前不让球赔率（主胜/平/客胜）</th><th>EV（主胜/平/客胜）</th><th>备注</th></tr></thead><tbody>
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


def send_telegram_link(env: Dict[str, str], report_url: str, generated_at: dt.datetime) -> None:
    if env.get("SEND_TELEGRAM", "0") not in {"1", "true", "TRUE", "yes"}:
        return
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.", file=sys.stderr)
        return
    text = (
        "世界杯每日预测更新\n"
        f"生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"HTML 报告地址：{report_url}\n"
        "完整内容请打开 HTML 报告查看。"
    )
    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def post(opener: request.OpenerDirector) -> None:
        req = request.Request(url, data=payload, method="POST")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--odds-html", help="Use a local odds HTML file instead of fetching")
    args = parser.parse_args()

    env = {**os.environ, **load_env(ROOT / args.env)}
    odds_url = env.get("ODDS_URL", DEFAULT_ODDS_URL)
    report_path = ROOT / env.get("REPORT_PATH", "docs/worldcup-2026-agent-report.html")
    report_url = env.get("REPORT_URL") or report_path.resolve().as_uri()

    model = load_model(ROOT / "config" / "model_probabilities.json")
    if args.odds_html:
        page = Path(args.odds_html).read_text(encoding="utf-8", errors="ignore")
    else:
        page = fetch_text(odds_url)

    rows = parse_odds_page(page, model)
    if not rows:
        print("No model-covered World Cup odds rows were parsed.", file=sys.stderr)

    generated_at = dt.datetime.now()
    report = generate_report(rows, odds_url, generated_at)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    send_telegram_link(env, report_url, generated_at)
    print(f"Report written: {report_path}")
    print(f"Report URL: {report_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

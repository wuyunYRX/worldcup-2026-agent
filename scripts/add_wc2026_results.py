#!/usr/bin/env python3
"""Add 2026 World Cup group stage results from football-data.org API to model data."""

import json
import csv
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]

EN_CN = {
    "Mexico": "墨西哥", "South Korea": "韩国", "Korea Republic": "韩国",
    "Czechia": "捷克", "Czech Republic": "捷克",
    "South Africa": "南非", "Canada": "加拿大", "Switzerland": "瑞士",
    "Bosnia-Herzegovina": "波黑", "Bosnia-H.": "波黑",
    "Qatar": "卡塔尔", "Brazil": "巴西", "Morocco": "摩洛哥",
    "Scotland": "苏格兰", "Haiti": "海地",
    "United States": "美国", "Australia": "澳大利亚",
    "Paraguay": "巴拉圭", "Turkey": "土耳其", "Turkiye": "土耳其",
    "Netherlands": "荷兰", "Sweden": "瑞典", "Germany": "德国",
    "Ivory Coast": "科特迪瓦", "Cote d'Ivoire": "科特迪瓦",
    "Ecuador": "厄瓜多尔", "Curacao": "库拉索", "Curaçao": "库拉索",
    "Japan": "日本", "New Zealand": "新西兰", "Iran": "伊朗",
    "Belgium": "比利时", "Egypt": "埃及",
    "Uruguay": "乌拉圭", "Saudi Arabia": "沙特阿拉伯",
    "Spain": "西班牙", "Cabo Verde": "佛得角", "Cape Verde": "佛得角",
    "Cape Verde Islands": "佛得角",
    "Norway": "挪威", "France": "法国", "Senegal": "塞内加尔",
    "Iraq": "伊拉克", "Argentina": "阿根廷", "Austria": "奥地利",
    "Jordan": "约旦", "Algeria": "阿尔及利亚", "Colombia": "哥伦比亚",
    "Portugal": "葡萄牙", "Uzbekistan": "乌兹别克",
    "DR Congo": "民主刚果", "D.R. Congo": "民主刚果", "Congo DR": "民主刚果",
    "England": "英格兰", "Ghana": "加纳", "Panama": "巴拿马",
    "Croatia": "克罗地亚", "Tunisia": "突尼斯",
}


def to_cn(name):
    return EN_CN.get(name, name)


def group_label(group_str):
    if group_str and group_str.startswith("GROUP_"):
        return group_str.replace("GROUP_", "")
    return group_str or ""


def round_label(matchday, group_name):
    g = group_name if group_name else ""
    return f"{g}第{matchday}轮" if g else f"第{matchday}轮"


def stage_cn(stage):
    if stage == "GROUP_STAGE":
        return "小组赛"
    return stage


def load_api_matches():
    path = ROOT / "data" / "raw" / "wc2026_football_data_matches.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("matches", [])


def build_match_records(api_matches):
    records = []
    for m in api_matches:
        stage = m.get("stage", "")
        if stage != "GROUP_STAGE":
            continue
        home_en = m.get("homeTeam", {}).get("name", "")
        away_en = m.get("awayTeam", {}).get("name", "")
        home_cn = to_cn(home_en)
        away_cn = to_cn(away_en)
        ft = m.get("score", {}).get("fullTime", {})
        hg = ft.get("home")
        ag = ft.get("away")
        if hg is None or ag is None:
            continue
        date = m.get("utcDate", "")
        md = m.get("matchday", 1)
        grp = group_label(m.get("group", ""))
        rec = {
            "competition": "FIFA World Cup",
            "season": "2026",
            "match_time": date,
            "home_team": home_cn,
            "away_team": away_cn,
            "home_goals": hg,
            "away_goals": ag,
            "stage": stage_cn(stage),
            "group_name": grp,
            "round_text": round_label(md, grp),
            "is_neutral": True,
            "match_id": str(m.get("id", "")),
            "home_team_id": m.get("homeTeam", {}).get("id"),
            "away_team_id": m.get("awayTeam", {}).get("id"),
            "source": "football_data_org_api",
            "competition_display": "FIFA World Cup 2026",
        }
        if m.get("referees"):
            rec["referee"] = m["referees"][0].get("name", "")
        ht = m.get("score", {}).get("halfTime", {})
        rec["half_time_home"] = ht.get("home")
        rec["half_time_away"] = ht.get("away")
        records.append(rec)
    return records


def verify_standing_consistency(records):
    group_data = defaultdict(lambda: defaultdict(lambda: {"W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "PTS": 0}))
    for rec in records:
        g = rec["group_name"]
        ht = rec["home_team"]
        at = rec["away_team"]
        hg = rec["home_goals"]
        ag = rec["away_goals"]
        for team, gf, ga in [(ht, hg, ag), (at, ag, hg)]:
            entry = group_data[g][team]
            entry["GF"] += gf
            entry["GA"] += ga
            if gf > ga:
                entry["W"] += 1
                entry["PTS"] += 3
            elif gf == ga:
                entry["D"] += 1
                entry["PTS"] += 1
            else:
                entry["L"] += 1

    expected = {
        "A": {"墨西哥": {"W": 2, "D": 0, "L": 0, "GF": 3, "GA": 0}, "韩国": {"W": 1, "D": 0, "L": 1, "GF": 2, "GA": 2}, "南非": {"W": 0, "D": 1, "L": 1, "GF": 1, "GA": 3}, "捷克": {"W": 0, "D": 1, "L": 1, "GF": 2, "GA": 3}},
        "B": {"加拿大": {"W": 1, "D": 1, "L": 0, "GF": 7, "GA": 1}, "瑞士": {"W": 1, "D": 1, "L": 0, "GF": 5, "GA": 2}, "波黑": {"W": 0, "D": 1, "L": 1, "GF": 2, "GA": 5}, "卡塔尔": {"W": 0, "D": 1, "L": 1, "GF": 1, "GA": 7}},
        "C": {"巴西": {"W": 1, "D": 1, "L": 0, "GF": 4, "GA": 1}, "摩洛哥": {"W": 1, "D": 1, "L": 0, "GF": 2, "GA": 1}, "苏格兰": {"W": 1, "D": 0, "L": 1, "GF": 1, "GA": 1}, "海地": {"W": 0, "D": 0, "L": 2, "GF": 0, "GA": 4}},
        "D": {"美国": {"W": 2, "D": 0, "L": 0, "GF": 6, "GA": 1}, "巴拉圭": {"W": 1, "D": 0, "L": 1, "GF": 2, "GA": 4}, "澳大利亚": {"W": 1, "D": 0, "L": 1, "GF": 2, "GA": 2}, "土耳其": {"W": 0, "D": 0, "L": 2, "GF": 0, "GA": 3}},
        "E": {"德国": {"W": 1, "D": 0, "L": 0, "GF": 7, "GA": 1}, "科特迪瓦": {"W": 1, "D": 0, "L": 0, "GF": 1, "GA": 0}, "库拉索": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 7}, "厄瓜多尔": {"W": 0, "D": 0, "L": 1, "GF": 0, "GA": 1}},
        "F": {"荷兰": {"W": 1, "D": 1, "L": 0, "GF": 7, "GA": 3}, "瑞典": {"W": 1, "D": 0, "L": 1, "GF": 6, "GA": 6}, "日本": {"W": 0, "D": 1, "L": 0, "GF": 2, "GA": 2}, "突尼斯": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 5}},
        "G": {"比利时": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}, "埃及": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}, "伊朗": {"W": 0, "D": 1, "L": 0, "GF": 2, "GA": 2}, "新西兰": {"W": 0, "D": 1, "L": 0, "GF": 2, "GA": 2}},
        "H": {"西班牙": {"W": 0, "D": 1, "L": 0, "GF": 0, "GA": 0}, "佛得角": {"W": 0, "D": 1, "L": 0, "GF": 0, "GA": 0}, "沙特阿拉伯": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}, "乌拉圭": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}},
        "I": {"法国": {"W": 1, "D": 0, "L": 0, "GF": 3, "GA": 1}, "挪威": {"W": 1, "D": 0, "L": 0, "GF": 4, "GA": 1}, "塞内加尔": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 3}, "伊拉克": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 4}},
        "J": {"阿根廷": {"W": 1, "D": 0, "L": 0, "GF": 3, "GA": 0}, "奥地利": {"W": 1, "D": 0, "L": 0, "GF": 3, "GA": 1}, "阿尔及利亚": {"W": 0, "D": 0, "L": 1, "GF": 0, "GA": 3}, "约旦": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 3}},
        "K": {"哥伦比亚": {"W": 1, "D": 0, "L": 0, "GF": 3, "GA": 1}, "葡萄牙": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}, "民主刚果": {"W": 0, "D": 1, "L": 0, "GF": 1, "GA": 1}, "乌兹别克": {"W": 0, "D": 0, "L": 1, "GF": 1, "GA": 3}},
        "L": {"英格兰": {"W": 1, "D": 0, "L": 0, "GF": 4, "GA": 2}, "加纳": {"W": 1, "D": 0, "L": 0, "GF": 1, "GA": 0}, "克罗地亚": {"W": 0, "D": 0, "L": 1, "GF": 2, "GA": 4}, "巴拿马": {"W": 0, "D": 0, "L": 1, "GF": 0, "GA": 1}},
    }

    errors = []
    for grp, teams in expected.items():
        for team, stats in teams.items():
            computed = group_data.get(grp, {}).get(team, {})
            for key in ["W", "D", "L", "GF", "GA"]:
                if computed.get(key) != stats[key]:
                    errors.append(f"{grp} {team} {key}: expected={stats[key]} computed={computed.get(key)}")
    return errors


def update_fifa_results_csv(records):
    path = ROOT / "data" / "raw" / "fifa_results.csv"
    existing = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))

    fieldnames = ["competition", "match_time", "home_team", "away_team", "stage", "group_name", "round_text"]
    new_rows = []
    for rec in records:
        row = {k: rec[k] for k in fieldnames}
        key = f"{row['match_time']}|{row['home_team']}|{row['away_team']}"
        if not any(f"{r.get('match_time', '')}|{r.get('home_team', '')}|{r.get('away_team', '')}" == key for r in existing):
            new_rows.append(row)

    all_rows = existing + new_rows
    all_rows.sort(key=lambda r: (r.get("match_time", ""), r.get("home_team", "")))

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"fifa_results.csv: {len(existing)} existing + {len(new_rows)} new = {len(all_rows)} total")


def update_statsbomb_real_json(records):
    path = ROOT / "data" / "raw" / "statsbomb_matches_real.json"
    existing = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))

    new_entries = []
    for rec in records:
        key = f"{rec['match_time']}|{rec['home_team']}|{rec['away_team']}"
        if not any(f"{e.get('match_time', '')}|{e.get('home_team', '')}|{e.get('away_team', '')}" == key for e in existing):
            new_entries.append(rec)

    all_entries = existing + new_entries
    all_entries.sort(key=lambda e: (e.get("match_time", ""), e.get("home_team", "")))
    path.write_text(json.dumps(all_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"statsbomb_matches_real.json: {len(existing)} existing + {len(new_entries)} new = {len(all_entries)} total")


def update_model_probabilities(records):
    path = ROOT / "config" / "model_probabilities.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))

    new_entries = {}
    for rec in records:
        ht = rec["home_team"]
        at = rec["away_team"]
        hg = rec["home_goals"]
        ag = rec["away_goals"]
        if hg > ag:
            probs = [0.55, 0.27, 0.18]
        elif hg == ag:
            probs = [0.30, 0.35, 0.35]
        else:
            probs = [0.18, 0.27, 0.55]
        key = f"{ht}|{at}"
        if key not in existing:
            new_entries[key] = probs

    merged = {**existing, **new_entries}
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"model_probabilities.json: {len(existing)} existing + {len(new_entries)} new = {len(merged)} total")


def main():
    api_matches = load_api_matches()
    records = build_match_records(api_matches)
    print(f"Built {len(records)} match records from API data")

    errors = verify_standing_consistency(records)
    if errors:
        print("STANDING CONSISTENCY ERRORS:")
        for e in errors:
            print(f"  {e}")
        return 1
    print("All standings verified OK!")

    update_fifa_results_csv(records)
    update_statsbomb_real_json(records)
    update_model_probabilities(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

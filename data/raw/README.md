# 原始数据目录说明

该目录用于存放多数据源原始输入文件，供 `scripts/build_training_data.py` 构建训练主表。

## 当前支持的输入文件

- `statsbomb_matches.json`
  - 主比赛骨架源
  - 建议字段：`competition`, `season`, `match_time`, `home_team`, `away_team`, `home_goals`, `away_goals`, `stage`, `group_name`, `round`, `is_neutral`
- `elo_ratings.json` 或 `elo_ratings.csv`
  - 赛前 Elo 时间序列
  - 建议字段：`team`, `date`, `elo`
- `qtx_matches.json` 或 `qtx_matches.csv`
  - 补充分组、轮次、半场比分
  - 建议字段：`competition`, `match_time`, `home_team`, `away_team`, `stage`, `group_name`, `round_text`, `half_time_score`
- `zgzcw_odds.json` 或 `zgzcw_odds.csv`
  - 赔率补充
  - 建议字段：`competition`, `match_time`, `home_team`, `away_team`, `odds_home_win`, `odds_draw`, `odds_away_win`, `handicap`
- `fifa_results.json` 或 `fifa_results.csv`
  - 权威校验源
  - 建议字段：`competition`, `match_time`, `home_team`, `away_team`, `stage`, `group_name`, `round_text`
  - 用途：命中后将主表中的 `fifa_verified` 标记为 `true`，并优先用其阶段字段做校正
- `dqd_results.json` 或 `dqd_results.csv`
  - 懂球帝赛果补充/校验源
  - 建议字段：`competition`, `match_time`, `home_team`, `away_team`, `stage`, `group_name`, `round_text`
  - 用途：命中后将主表中的 `dqd_verified` 标记为 `true`，用于补赛果来源覆盖率统计

## 数据格式约定

- JSON 文件默认支持：
  - 顶层数组
  - 或 `{ "rows": [...] }` 结构
- CSV 文件默认使用 UTF-8 或 UTF-8 with BOM
- 时间字段建议统一为 ISO8601，例如：`2026-06-20T18:00:00Z`

## 第一阶段建议

先准备最小闭环：

1. `statsbomb_matches.json`
2. `elo_ratings.csv`

然后再逐步补：

3. `qtx_matches.csv`
4. `zgzcw_odds.csv`
5. `fifa_results.csv`
6. `dqd_results.csv`

## FIFA 校验文件建议

- 如果直接抓取 `fifa.com` 官方结果页不稳定，可以先手工导出或整理官方结果到 `fifa_results.csv`
- 为了让主表顺利命中，建议至少保证以下键字段稳定一致：
  - `competition`
  - `match_time`
  - `home_team`
  - `away_team`
- 如果有 `stage`、`group_name`、`round_text`，主表合并时会优先参考这些字段做阶段修正

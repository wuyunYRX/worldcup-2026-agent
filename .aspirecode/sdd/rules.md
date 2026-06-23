# AspireCode SDD 项目规则

初始化目标目录：`E:\work\worldcup-2026-agent`  
SDD 产物目录：`.aspirecode/sdd/`  
扫描源码 hash：`1172682b8d8c3bc4177fc1dcc16fd0f0e0538ee8b1a8055f532bc719a294d336`  
初始化日期：`2026-06-23`

## 项目概览

- 项目名称：`2026 World Cup Prediction Agent`。
- 项目用途：抓取 2026 美加墨世界杯竞彩足球赔率和赛前信息，融合模型概率、市场隐含概率、比分模型、蒙特卡洛验证、赛后校准，生成 HTML/PNG 报告和预测快照。
- 核心入口：`src/worldcup_agent.py`。
- 日常入口：`run.sh`、`daily_pipeline.sh`、`review.sh`。
- 测试入口：`python -m unittest tests.test_core`。
- 风险边界：所有概率均为主观模型预测，不保证命中；涉及投注建议时必须控制风险，不得表达确定收益。

## 技术栈

| 类别 | 实际扫描结果 |
|---|---|
| 语言 | Python 3、Bash |
| 运行依赖 | `playwright>=1.44.0` |
| 标准库 | `argparse`、`csv`、`datetime`、`html`、`json`、`math`、`os`、`random`、`re`、`shutil`、`sys`、`uuid`、`pathlib`、`urllib` |
| 报告渲染 | HTML 生成，Playwright 截图 |
| 外部接口 | 足彩网、QTX、FIFA、football-data.org、Telegram Bot API、OpenAI-compatible LLM API |
| 测试框架 | `unittest` |
| 配置格式 | `.env`、JSON、Shell 环境变量 |

## 目录结构

| 路径 | 用途 |
|---|---|
| `src/worldcup_agent.py` | 主预测、赔率解析、概率融合、比分模型、报告生成、Telegram 发送 |
| `src/ai_probability_adjustment.py` | 赛前情报对胜平负概率的小幅可解释修正 |
| `src/probability_fusion.py` | 模型概率与市场概率融合 |
| `src/kelly_criterion.py` | Kelly 比例和建议投注额计算 |
| `scripts/fetch_prematch_team_news.py` | 从 QTX 情报页提取伤停、战术、身价、天气等赛前特征 |
| `scripts/map_qtx_match_tokens.py` | 从 QTX 赛程映射比赛 token |
| `scripts/fetch_latest_results.py` | 通过 football-data.org 拉取最新已完赛赛果 |
| `scripts/review_completed_matches.py` | 赛后复盘、指标统计、校准配置回填、反思报告生成 |
| `scripts/backfill_prematch_features.py` | 对历史样本回填赛前特征，支持 LLM API |
| `scripts/add_wc2026_results.py` | 补充/转换 2026 世界杯赛果数据 |
| `config/model_probabilities.json` | 胜平负基础模型概率配置 |
| `config/bayesian_calibration.json` | 概率校准、Kelly 风控、AI/天气修正参数 |
| `config/team_aliases.json` | 球队名称别名映射 |
| `config/team_value_profiles.json` | 球队身价、阵容深度等画像 |
| `models/score_model/score_model_v1.json` | 已训练比分模型及特征权重 |
| `data/raw/*.json` | 原始/中间数据：赛前情报、盘口、赛果等 |
| `docs/run/` | 运行报告、截图、预测快照、训练候选数据 |
| `docs/review/` | 赛后校准 JSON 和反思 Markdown |
| `tests/test_core.py` | 核心数学、解析、融合、天气修正、报告流程测试 |
| `.aspirecode/sdd/` | AspireCode SDD 项目级指令和规则产物 |

## 模块调用与依赖矩阵

| 调用方 | 被调用/依赖 | 作用 |
|---|---|---|
| `run.sh` | `src/worldcup_agent.py`、`scripts/map_qtx_match_tokens.py`、`scripts/fetch_prematch_team_news.py` | 一键生成预跑快照、映射 QTX token、抓取赛前情报、生成最终报告 |
| `daily_pipeline.sh` | `worldcup_agent.py`、`map_qtx_match_tokens.py`、`fetch_prematch_team_news.py`、可选 `backfill_prematch_features.py`、可选 `review_completed_matches.py` | 日常自动化流水线 |
| `review.sh` | `fetch_latest_results.py`、`review_completed_matches.py` | 拉取赛果并复盘 |
| `worldcup_agent.py` | `ai_probability_adjustment.adjust_probabilities_with_ai_context` | 基于伤停、轮换、战术、身价、天气修正 WDL 概率 |
| `worldcup_agent.py` | `probability_fusion.fuse_wdl_probabilities` | 融合模型概率与市场去水概率 |
| `worldcup_agent.py` | `kelly_criterion.*` | 计算 Kelly% 和建议投注额 |
| `worldcup_agent.py` | `data/raw/prematch_team_news.json` | 按 `match_time + home + away` 匹配赛前特征 |
| `worldcup_agent.py` | `models/score_model/score_model_v1.json` | 读取比分模型特征与权重，生成 `lambda_home/lambda_away` 和比分网格 |
| `fetch_prematch_team_news.py` | QTX 情报页、LLM API、`team_value_profiles.json` | 提取赛前伤停、战术、身价、天气特征并写入 `prematch_team_news.json` |
| `review_completed_matches.py` | `docs/run/*predictions*.json`、`data/raw/*matches*.json` | 匹配赛前预测与赛果，生成校准和反思报告 |
| `tests/test_core.py` | `src/` 与 `scripts/review_completed_matches.py` | 验证概率融合、Kelly、盘口解析、比分指标、天气修正等核心行为 |

## 存量 API 接口格式

### 命令行入口

| 命令 | 参数/环境 | 输出 |
|---|---|---|
| `python src/worldcup_agent.py` | `--env`、`--odds-html`、`--days`、`--qtx-supplement`、`--latest-copy`、`--skip-screenshot` | HTML 报告、预测快照 JSON、训练候选 JSON、可选 PNG 截图/Telegram |
| `python scripts/fetch_prematch_team_news.py` | 读取 `.env`、最新预测快照、QTX token | `data/raw/prematch_team_news.json` |
| `python scripts/map_qtx_match_tokens.py` | `QTX_WORLD_CUP_URL` | 更新最新预测快照中的 QTX token |
| `python scripts/fetch_latest_results.py` | `FOOTBALL_DATA_API_TOKEN`、`FOOTBALL_DATA_MATCHES_URL` | `data/raw/wc2026_football_data_matches.json` |
| `python scripts/review_completed_matches.py` | 本地预测快照和赛果文件 | `docs/review/postmatch-calibration_*.json`、`docs/review/postmatch-reflection_*.md`、更新 `config/bayesian_calibration.json` |
| `python scripts/backfill_prematch_features.py` | `--env`、`--limit`、`--start-index`、`--llm-timeout`、`--llm-retries`、`--llm-smoke-test` | `data/raw/prematch_training_samples.json` 或 LLM 烟测结果 |

### 关键环境变量

| 变量 | 用途 |
|---|---|
| `ODDS_URL` | 足彩网竞彩足球胜平负/让球页面 |
| `QTX_WORLD_CUP_URL` | QTX 世界杯赛程页 |
| `QTX_QINGBAO_BASE_URL` | QTX 情报页基础地址 |
| `FIFA_SCORES_URL` | FIFA 赛程/比分页面 |
| `FOOTBALL_DATA_MATCHES_URL` | football-data.org 赛果接口 |
| `FOOTBALL_DATA_API_TOKEN` | football-data.org API token |
| `SEND_TELEGRAM`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` | Telegram 报告发送 |
| `LLM_API_KEY`、`ZHIPU_API_KEY`、`OPENAI_API_KEY` | LLM API Key，代码按顺序读取 |
| `LLM_BASE_URL`、`LLM_MODEL` | OpenAI-compatible LLM base URL 和模型名 |
| `ENABLE_AI_PROBABILITY_ADJUSTMENT` | 是否启用赛前情报概率修正 |
| `AI_PROBABILITY_MAX_DELTA`、`AI_PROBABILITY_HIGH_CONFIDENCE_DELTA` | AI 修正幅度上限 |
| `ENABLE_ZGZCW_ASIAN_HANDICAP`、`ENABLE_ZGZCW_TOTAL_GOALS` | 是否抓取外部亚盘/大小球盘口 |
| `ENABLE_MONTE_CARLO`、`MONTE_CARLO_SIMULATIONS`、`MONTE_CARLO_SEED`、`MONTE_CARLO_LAMBDA_SIGMA` | 蒙特卡洛验证参数 |

### 关键 JSON 数据结构

| 文件 | 结构要点 |
|---|---|
| `data/raw/prematch_team_news.json` | 列表；每项含 `match_time`、`home_team`、`away_team`、伤停、停赛、阵容、轮换、战术、身价、天气字段 |
| `config/bayesian_calibration.json` | 风控和校准参数；含 `model_weight`、Kelly 参数、平局/弱队概率校准、AI/身价/天气修正上限、`calibration` 指标 |
| `models/score_model/score_model_v1.json` | `model_version`、`feature_names`、`home_model.weights`、`away_model.weights`、`score_correlation` |
| `docs/run/worldcup-2026-agent-predictions_*.json` | `generated_at`、`odds_url`、`matches[]`；比赛项含概率、赔率、EV、Kelly、比分预测、盘口、赛前修正 |
| `docs/run/worldcup-2026-agent-training-candidates_*.json` | 训练候选样本；包含概率、盘口、赛前特征、lambda、比分候选等 |
| `docs/review/postmatch-calibration_*.json` | 复盘指标；含 WDL 准确率、Brier、logloss、比分 Top1/Top3、偏差诊断 |

## 错误码与统一响应

本项目不是 Web 服务，未发现统一 HTTP 响应体或集中错误码枚举。实际错误处理如下：

| 场景 | 当前行为 |
|---|---|
| `worldcup_agent.py` 未解析到比赛 | 向 stderr 输出 `No World Cup odds rows were parsed.`，仍生成报告 |
| 截图失败且未允许缺失 | 输出错误提示并返回进程码 `2` |
| Telegram 发送失败 | 捕获异常，向 stderr 输出失败类型和错误信息，不阻塞主报告生成 |
| QTX token 映射/赛前情报抓取失败 | `daily_pipeline.sh` 使用 `warn_or_fail`，默认告警不中断，可通过 required 环境变量强制失败 |
| LLM API 异常 | `backfill_prematch_features.py` 识别 HTTP/HTML/模型不可用等失败，返回失败原因或降级为空特征 |
| `run.sh` 中途失败 | Bash `set -euo pipefail` 终止，当前脚本会显示中文失败步骤并等待按键关闭 |

建议新增接口时保持：明确返回结构、不要吞掉关键异常、对外部服务失败提供本地降级路径。

## 伪码/编码输入

- 初始化命令来源：用户在 `E:\work\worldcup-2026-agent` 执行 `/ac-init`，要求产物写入 `.aspirecode/sdd/`。
- 独立伪码文件：未发现单独伪码文件。
- 需求来源：用户命令、`README.md`、实际源码与配置扫描。
- 扫描文件范围：`README.md`、`requirements.txt`、`run.sh`、`daily_pipeline.sh`、`review.sh`、`src/*.py`、`scripts/*.py`、`tests/test_core.py`、`config/bayesian_calibration.json` 等核心文件。
- 扫描源码 hash：`1172682b8d8c3bc4177fc1dcc16fd0f0e0538ee8b1a8055f532bc719a294d336`。

## GitNexus 状态

- GitNexus MCP 健康检查：可访问，`gitnexus_list_repos` 返回已索引仓库列表。
- 当前目标仓库 `E:\work\worldcup-2026-agent`：未出现在已索引仓库列表中。
- 初始化降级策略：本次规则生成降级为本地源码扫描，未阻塞产物生成。
- 后续建议：如需图谱级调用链、影响分析或跨模块 blast radius，请先将当前仓库加入 GitNexus 索引，再重新执行健康检查。

## 开发与验证规则

- 修改核心预测逻辑后至少运行：`python -m unittest tests.test_core`。
- 修改主入口、脚本或导入关系后建议运行：`python -m py_compile src/worldcup_agent.py src/kelly_criterion.py src/probability_fusion.py scripts/review_completed_matches.py scripts/backfill_prematch_features.py`。
- 修改 `run.sh`、`daily_pipeline.sh`、`review.sh` 后至少运行 `bash -n <script>`。
- 涉及 `.env`、OpenCode 配置或 API Key 时，只检查结构，不在回复中展示密钥值。
- 不要提交 `docs/run/`、`docs/review/`、临时 HTML/PNG/JSON 等运行产物，除非用户明确要求。

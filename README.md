# 2026 World Cup Prediction Agent

一个用于 2026 美加墨世界杯的预测与竞彩足球预测 Agent。

它会抓取当前可售世界杯竞彩场次赔率，结合模型胜平负概率、比分模型、市场隐含概率和赛后校准生成结构化 HTML 报告。报告优先展示未来 24 小时赛果预测、比分 Top3、进球倾向、小组变化和赛后校准；赔率 EV/Kelly 作为辅助参考。

> 所有概率都是模型主观预测，不保证命中，不是官方赔率。涉及购彩请控制预算、少串关，不要把预测当确定收益。

## 功能

- 抓取足彩网竞彩足球胜平负/让球页面
- 解析可售世界杯场次、比赛编号、开赛时间、让球数、普通胜平负赔率、让球胜平负赔率
- 基于模型概率和市场赔率计算：
  - 市场隐含概率（赔率去水）
  - 融合概率 = 市场概率 + model_weight × (模型概率 - 市场概率)
  - EV = 融合概率 × 赔率 - 1
  - Kelly% 与建议投注额（含单注/总投入上限）
  - 基于比分模型的最可能比分、大小球和双方进球概率
- 报告主线是赛果预测，EV/Kelly 只作为辅助风控信息
- 默认开启 AI 赛前情报修正：只要存在赛前情报，就会对 WDL 概率做小幅可解释修正
- 支持赛后复盘，输出 WDL 准确率、Brier、logloss、比分 Top1/Top3，并回填风控校准配置
- 支持 AI 回填赛前伤停/停赛特征，优先使用 OpenAI `responses` 接口
- 生成完整 HTML 报告和 2x 高清报告截图
- 可选同步 Telegram，以文件形式发送高清 PNG 原图
- 可用 cron 或 macOS launchd 设置每天 09:00、18:00 自动更新

## 新增功能备注

- 报告文件默认使用时间戳命名，HTML、PNG、预测快照和训练候选数据都可按运行批次追溯。
- 默认只预测未来 1 天可售比赛；`QTX补充` 改为可选，不会挤占主预测场次。
- 报告已接入小组实时积分和最佳第三名排序，基于 `data/raw/wc2026_football_data_matches.json` 中的已完赛结果计算。
- EV 使用融合概率计算：`EV = 融合概率 × 赔率 - 1`；Kelly 风控支持资金量、单注上限、总投入上限和最小 EV 门槛。
- 赛后复盘会生成校准与反思报告，并在样本不足或命中偏弱时只收紧 Kelly 风控，避免过早调整模型权重。
- 为减少 GitHub 提交噪音，探索脚本、训练中间产物和运行报告已清理或加入忽略，仅保留生产运行必需脚本和核心测试。

## 一键安装

```bash
git clone https://github.com/<your-name>/worldcup-2026-agent.git
cd worldcup-2026-agent
./install.sh
```

## 维护者发布到 GitHub

如果你是在本地首次发布这个项目，先登录 GitHub CLI：

```bash
gh auth login
```

然后在项目目录执行：

```bash
gh repo create worldcup-2026-agent --public --source=. --remote=origin --push
```

发布成功后，把上方“一键安装”里的 `<your-name>` 改成你的 GitHub 用户名或组织名。

安装后编辑 `.env`：

```bash
cp .env.example .env
open .env
```

运行一次：

```bash
./run.sh
```

常用网站地址统一在 `.env` 管理：

```env
ODDS_URL=https://cp.zgzcw.com/lottery/jchtplayvsForJsp.action?lotteryId=47&type=jcmini
QTX_WORLD_CUP_URL=https://www.qtx.com/worldcup/
QTX_QINGBAO_BASE_URL=https://live.qtx.com/qingbao
FIFA_SCORES_URL=https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures
FOOTBALL_DATA_MATCHES_URL=https://api.football-data.org/v4/competitions/2000/matches?season=2026&status=FINISHED
TELEGRAM_API_BASE_URL=https://api.telegram.org
ENABLE_AI_PROBABILITY_ADJUSTMENT=1
AI_PROBABILITY_MAX_DELTA=0.03
AI_PROBABILITY_HIGH_CONFIDENCE_DELTA=0.05
```

日常安全流水线（推荐用于定时任务）：

```bash
./daily_pipeline.sh
```

默认不会执行 AI 回填和赛后复盘，只会完成赔率抓取、赛前资讯、最终报告生成。需要启用小批量 AI 回填时：

```bash
RUN_AI_BACKFILL=1 AI_BACKFILL_LIMIT=10 ./daily_pipeline.sh
```

需要在赛果更新后同步跑复盘：

```bash
RUN_POSTMATCH_REVIEW=1 ./daily_pipeline.sh
```

本地测试：

```bash
python -m py_compile src/worldcup_agent.py src/kelly_criterion.py src/probability_fusion.py scripts/review_completed_matches.py scripts/backfill_prematch_features.py
python -m unittest tests.test_core
```

CI 会在 GitHub Actions 中执行同样的编译和单元测试流程。

生成的 HTML 报告默认带时间戳输出到：

```text
docs/run/worldcup-2026-agent-report_YYYYMMDD_HHMMSS.html
```

生成的报告截图默认带时间戳输出到：

```text
docs/run/worldcup-2026-agent-report_YYYYMMDD_HHMMSS.png
```

## AI 赛前信息配置

`scripts/backfill_prematch_features.py` 支持通过 OpenAI-compatible API 回填伤停/停赛等赛前特征。在 `.env` 中配置：

```env
LLM_API_KEY=你的API密钥
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-flash
```

脚本优先使用 OpenAI `responses` 接口（`/v1/responses`），并在失败时回退到 Chat Completions。若使用其他聚合平台，将 `LLM_BASE_URL` 改为该平台的 OpenAI-compatible base URL，例如：

```env
LLM_BASE_URL=https://xfastapi.ai/v1
LLM_MODEL=gpt-5.5
```

先运行烟测确认可用：

```bash
python scripts/backfill_prematch_features.py --env .env --llm-smoke-test
```

建议先小批量回填，确认输出格式和 API 稳定性：

```bash
python scripts/backfill_prematch_features.py --env .env --limit 10 --llm-timeout 120 --llm-retries 1
```

如需从中间断点继续：

```bash
python scripts/backfill_prematch_features.py --env .env --start-index 100 --limit 50 --llm-timeout 120 --llm-retries 1
```

烟测成功时会返回 JSON，例如 `{"ok": true, "api": "responses"}`。若返回 `gateway_forbidden_1010`，通常是平台网关、账号资源池、模型权限或 IP 策略问题；代码已能识别该类失败，但需要在平台控制台确认 API Key 可用模型和访问策略。

## 赛后复盘与校准

比赛结果更新后，运行：

```bash
./review.sh
```

`review.sh` 会先尝试用 `FOOTBALL_DATA_API_TOKEN` 拉取最新已完赛赛果，写入 `data/raw/wc2026_football_data_matches.json`；如果未配置 token 或接口失败，会跳过联网更新并继续使用本地赛果文件复盘。

复盘脚本会扫描 `docs/run/worldcup-2026-agent-predictions_*.json` 中的赛前快照，匹配 `data/raw/wc2026_football_data_matches.json` 和历史赛果，输出：

- `docs/review/postmatch-calibration_*.json`：结构化校准结果，包括 WDL 准确率、Brier、logloss、比分 Exact/Top3。
- `docs/review/postmatch-reflection_*.md`：可读反思报告，列出命中、偏差和下一轮调整建议。
- `config/bayesian_calibration.json`：回填样本量和指标；样本较少且表现偏弱时，只收紧 Kelly 风控，不激进调整模型权重。

如需直接运行复盘脚本而不拉取赛果：

```bash
python scripts/review_completed_matches.py
```

## Telegram 配置

如果要把高清报告截图同步到 Telegram，在 `.env` 中填入：

```env
TELEGRAM_BOT_TOKEN=你的机器人token
TELEGRAM_CHAT_ID=8563592562
SEND_TELEGRAM=1
```

注意：Telegram 不再发送 HTML 地址或完整 HTML 表格，本项目默认用 `sendDocument` 发送高清 PNG 原图，避免 `sendPhoto` 压缩导致长图发糊。

## 定时运行

### cron 示例

每天北京时间/本机时间 09:00 和 18:00 运行：

```cron
0 9,18 * * * cd /path/to/worldcup-2026-agent && ./daily_pipeline.sh >> logs/agent.log 2>&1
```

### macOS launchd

安装脚本会生成 `launchd/worldcup-2026-agent.plist.example`，可按里面注释复制到：

```text
~/Library/LaunchAgents/com.local.worldcup-2026-agent.plist
```

然后执行：

```bash
launchctl load ~/Library/LaunchAgents/com.local.worldcup-2026-agent.plist
```

## 报告区块

HTML 报告固定包含：

- `summary` 本轮摘要
- `matches-24h` 今日/未来 24 小时重点预测
- `result-predictions` 赛果预测总览
- `group-update` 小组名次和最佳第三名变化
- `live-postmatch` 赛前/实时/赛后更新
- `remaining-probabilities` 赔率与概率明细
- `ev-board` 辅助赔率、EV 与 Kelly 筛选
- `ticket` 辅助 Kelly 风控票单和建议投注额
- `changes` 与上一版变化
- `risks` 关键风险因素
- `sources` 来源

## 数据说明

当前版本以 `config/model_probabilities.json`、比分模型、市场赔率和 `config/bayesian_calibration.json` 共同驱动预测。生产使用时建议继续补强这些数据管道：

- Elo/Glicko 动态评分
- xG/xGA 与近期表现
- 旅行距离、休息天数、时差
- 天气、海拔、湿度、风速
- 首发、伤停、红黄牌和停赛
- 赔率变动监控
- 更完整的比分/净胜球校准模型

## 免责声明

本项目只用于数据分析和研究，不构成任何投注、投资或收益承诺。彩票存在随机性和亏损风险，请理性购彩。

# 2026 World Cup Prediction Agent

一个用于 2026 美加墨世界杯的预测与竞彩足球预测 Agent。

它会抓取当前可售世界杯竞彩场次赔率，结合内置模型主观胜平负概率计算 EV，并生成结构化 HTML 报告。报告包含未来 24 小时重点比赛、小组变化、剩余可售场次概率表、赔率 EV 筛选和保守购彩建议。

> 所有概率都是模型主观预测，不保证命中，不是官方赔率。涉及购彩请控制预算、少串关，不要把预测当确定收益。

## 功能

- 抓取足彩网竞彩足球胜平负/让球页面
- 解析可售世界杯场次、比赛编号、开赛时间、让球数、普通胜平负赔率、让球胜平负赔率
- 基于模型概率计算：
  - EV = 模型概率 × 赔率 - 1
  - 保本赔率 = 1 / 模型概率
  - 正期望/接近正期望候选项
- 生成完整 HTML 报告和 2x 高清报告截图
- 可选同步 Telegram，以文件形式发送高清 PNG 原图
- 可用 cron 或 macOS launchd 设置每天 09:00、18:00 自动更新

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

生成的 HTML 报告默认在：

```text
docs/worldcup-2026-agent-report.html
```

生成的报告截图默认在：

```text
docs/worldcup-2026-agent-report.png
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
0 9,18 * * * cd /path/to/worldcup-2026-agent && ./run.sh >> logs/agent.log 2>&1
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
- `matches-24h` 今日/未来 24 小时重点比赛
- `group-update` 小组名次和最佳第三名变化
- `live-postmatch` 赛前/实时/赛后更新
- `remaining-probabilities` 当前剩余可售世界杯场次模型胜平负概率
- `ev-board` 赔率与 EV 筛选
- `ticket` 最终建议票单
- `changes` 与上一版变化
- `risks` 关键风险因素
- `sources` 来源

## 数据说明

当前版本内置了一组示例模型概率，用于演示完整工作流。生产使用时建议替换为你自己的模型服务或数据管道：

- Elo/Glicko 动态评分
- xG/xGA 与近期表现
- 旅行距离、休息天数、时差
- 天气、海拔、湿度、风速
- 首发、伤停、红黄牌和停赛
- 赔率变动监控

## 免责声明

本项目只用于数据分析和研究，不构成任何投注、投资或收益承诺。彩票存在随机性和亏损风险，请理性购彩。

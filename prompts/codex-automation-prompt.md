# Codex Automation Prompt

每天北京时间 09:00 和 18:00 更新 2026 美加墨世界杯预测与中国体育彩票购买建议。

每次运行先核对最新官方赛程、赛果、积分、小组出线规则、首发阵容、伤病/停赛、天气、红黄牌、换人和可靠赛事数据；如部分信息缺失，明确标注不确定性。

同时必须抓取当前竞彩足球赔率：优先读取 <https://cp.zgzcw.com/lottery/jchtplayvsForJsp.action?lotteryId=47&type=jcmini> 中的可售世界杯场次，提取比赛编号、主队、客队、开赛时间、让球数、不让球胜平负赔率、让球胜平负赔率。若普通请求无法解析，再尝试查找页面内接口或说明抓取失败原因。不要把旧赔率当最新赔率。

基于模型胜率和最新信息，逐场计算：

1. 模型主观胜平负概率
2. 当前赔率
3. EV = 模型概率 × 赔率 - 1
4. 保本赔率 = 1 / 模型概率
5. 是否正期望
6. 单关、复式、2串1是否值得买

让球玩法只有在有明确比分/净胜球概率依据时才推荐，否则优先普通胜平负。

输出报告格式必须为 HTML，保存到 `docs/worldcup-2026-agent-report.html`。不要用 Markdown 表格作为主体报告；使用 HTML 标题、段落、列表和表格。

HTML 必须包含：

```html
<h1>世界杯每日预测与购彩报告</h1>
<section id="summary">本轮摘要</section>
<section id="matches-24h">今日/未来 24 小时重点比赛</section>
<section id="group-update">小组名次和最佳第三名变化</section>
<section id="live-postmatch">赛前/实时/赛后更新</section>
<section id="remaining-probabilities">当前剩余可售世界杯场次模型胜平负概率</section>
<section id="ev-board">赔率与 EV 筛选</section>
<section id="ticket">最终建议票单</section>
<section id="changes">与上一版变化</section>
<section id="risks">关键风险因素</section>
<section id="sources">来源</section>
```

每次完成分析并生成 HTML 报告后，生成 2x 高清 PNG 截图，并通过 Telegram `sendDocument` 上传 PNG 原图到机器人当前绑定的聊天，不发送 HTML 全文，也不发送 HTML 地址。避免使用 `sendPhoto`，因为 Telegram 会压缩长图导致文字不清晰。

所有概率都必须标注为模型主观预测，不是保证命中或官方赔率。涉及购彩时必须提醒控制预算、少串关、不要把预测当确定收益。

# AspireCode Agent 指令

## 产物位置

- 本项目所有 AspireCode SDD 初始化产物必须写入 `.aspirecode/sdd/*`。
- 禁止把项目级初始化产物写入仓库根目录、worktree 根目录或 `.aspirecode/sdd/` 之外的位置。
- 本文件为项目级 Agent 指令入口；详细规则按需读取 `.aspirecode/sdd/rules.md`。

## 规则优先级

1. 系统/开发者/用户的显式指令优先级最高。
2. `.aspirecode/sdd/AGENTS.md` 为项目级强约束指令。
3. `.aspirecode/sdd/rules.md` 为详细项目规则与事实依据。
4. 当规则冲突时，按以上顺序执行；禁止用低优先级规则覆盖高优先级规则。

## 渐进式披露索引

- 快速入口：先阅读本文件，确认产物目录、优先级和工作边界。
- 项目事实：需要了解项目结构、技术栈、模块关系、运行命令时，读取 `.aspirecode/sdd/rules.md` 的“项目概览”“技术栈”“目录结构”。
- 改动前分析：涉及预测链路、赛前情报、赔率解析、报告生成、复盘校准时，读取 `.aspirecode/sdd/rules.md` 的“模块调用与依赖矩阵”。
- 接口与数据：涉及外部数据源、环境变量、输出文件、JSON 结构时，读取 `.aspirecode/sdd/rules.md` 的“存量 API 接口格式”“错误码与统一响应”。
- 溯源与降级：需要确认初始化来源、hash、GitNexus 状态时，读取 `.aspirecode/sdd/rules.md` 的“伪码/编码输入”“GitNexus 状态”。

## 项目工作约束

- 修改代码前优先扫描实际文件，禁止凭空编造项目结构或接口。
- 保持现有 Python 脚本、Bash 脚本和 JSON 配置格式一致。
- 涉及预测结果、赔率、Kelly 或投注建议时，保留风险提示，不得承诺收益。
- 涉及密钥、`.env`、OpenCode 配置等敏感信息时，不得在回复中泄露具体值。

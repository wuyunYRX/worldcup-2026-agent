#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

TOTAL_STEPS=4
CURRENT_STEP=0
CURRENT_PERCENT=0
CURRENT_LABEL="尚未开始"

pause_before_exit() {
    status=$?
    rm -f "$FULL_ENV"
    echo
    if [ "$status" -eq 0 ]; then
        echo "进度 ${TOTAL_STEPS}/${TOTAL_STEPS}（100%）：全部执行完成"
        echo "全部执行完成，按任意键关闭窗口..."
    else
        echo "执行失败：停在 ${CURRENT_STEP}/${TOTAL_STEPS}（${CURRENT_PERCENT}%）：${CURRENT_LABEL}"
        echo "请检查上方错误信息，按任意键关闭窗口..."
    fi
    if [ -t 0 ]; then
        IFS= read -r -n 1 _ || true
        echo
    fi
    exit "$status"
}

start_step() {
    CURRENT_STEP="$1"
    CURRENT_PERCENT="$2"
    CURRENT_LABEL="$3"
    detail="$4"
    echo
    echo "进度 ${CURRENT_STEP}/${TOTAL_STEPS}（${CURRENT_PERCENT}%）：${CURRENT_LABEL}"
    echo "正在处理：${detail}"
}

finish_step() {
    echo "完成 ${CURRENT_STEP}/${TOTAL_STEPS}（${CURRENT_PERCENT}%）：${CURRENT_LABEL}"
}

if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

FULL_ENV="$(mktemp)"
trap pause_before_exit EXIT

start_step 1 25 "准备运行环境与优化参数" "启用总进球模型、蒙特卡洛验证和比分候选配置"
if [ -f ".env" ]; then
    cp ".env" "$FULL_ENV"
else
    : > "$FULL_ENV"
fi
cat >> "$FULL_ENV" <<'EOF'
ENABLE_ZGZCW_TOTAL_GOALS=1
ENABLE_MONTE_CARLO=1
MONTE_CARLO_SIMULATIONS=10000
MONTE_CARLO_SEED=202606
MONTE_CARLO_LAMBDA_SIGMA=0.10
SCORE_CANDIDATE_TOP_N=5
SCORE_REPORT_TOP_N=3
ENABLE_LLM_TACTICAL_ANALYSIS=1
ENABLE_LLM_PLAYER_VALUE_ANALYSIS=1
PREMATCH_SOURCE_CACHE=1
PREMATCH_LLM_CACHE=1
PREMATCH_LLM_TIMEOUT=20
TACTICAL_LLM_TIMEOUT=20
PLAYER_VALUE_LLM_TIMEOUT=20
EOF
finish_step

start_step 2 50 "生成赛前预测初版" "先生成基础预测快照，为后续比赛编号和情报抓取做准备"
"$PYTHON" src/worldcup_agent.py --env "$FULL_ENV" --skip-screenshot
finish_step

start_step 3 75 "匹配比赛编号并拉取赛前情报" "匹配 QTX 比赛编号，然后抓取伤停、战术、身价和天气相关赛前信息"
"$PYTHON" scripts/map_qtx_match_tokens.py
"$PYTHON" scripts/fetch_match_weather.py
"$PYTHON" scripts/fetch_prematch_team_news.py
finish_step

start_step 4 100 "生成最终预测报告" "结合赔率、赛前情报、天气因素和蒙特卡洛验证生成最终报告"
"$PYTHON" src/worldcup_agent.py --env "$FULL_ENV"
finish_step

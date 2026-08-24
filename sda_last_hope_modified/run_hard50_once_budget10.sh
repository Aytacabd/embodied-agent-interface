#!/bin/bash
# run_hard50_once_budget10.sh
# =============================
# Runs the hard-50 benchmark ONCE with repair budget=10 (the max you'll
# ever test), scores that single run for real, then pulls back everything
# analyze_budget_stabilization.py needs to reconstruct what Task SR would
# have been at every budget from 1 to 10 — WITHOUT re-running anything.
#
# Why one run is enough: the repair loop never knows how much budget is
# left when it picks its next fix, so the sequence of repairs a task goes
# through up to attempt N is identical no matter whether the ceiling is N,
# N+3, or 10. A task that needed 4 repairs to succeed would have succeeded
# identically under budget=4, 5, ... or 10 — the loop just stops there
# either way. So this single run already contains every smaller budget's
# outcome; we just have to count, per task, how many repairs it actually
# used before its final (successful-or-not) state.
#
# Usage:
#   ./run_hard50_once_budget10.sh
# Then:
#   python3 analyze_budget_stabilization.py

set -euo pipefail

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "ERROR: OPENAI_API_KEY is not set in your shell." >&2
  echo "  Run:  export OPENAI_API_KEY=\"sk-...your key...\"" >&2
  echo "  ...in this same terminal, then re-run this script." >&2
  exit 1
fi

CONTAINER="bd0e40ed487c"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/opt/iGibson/sda_eai"
LOG_DIR="$LOCAL_DIR/run_fixed2_results/hard50_budget10"
MODEL="${HARD_MODEL:-gpt-4o-mini}"
BUDGET=10
MODEL_NAME="${MODEL}-sda-tree_hard50_r${BUDGET}"

mkdir -p "$LOG_DIR"

echo "==> Deploying current sda_last_hope_modified/ into container..."
docker exec "$CONTAINER" mkdir -p "$REMOTE_DIR"
docker cp "$LOCAL_DIR/." "$CONTAINER:$REMOTE_DIR"

echo "==> Running all 50 hard tasks with repair budget=$BUDGET (tag: $MODEL_NAME) ..."
run_log="$LOG_DIR/run_hard50_r${BUDGET}.log"
docker exec \
  -e "HARD_MODEL=$MODEL" \
  -e "HARD_MAX_REPLAN=$BUDGET" \
  -e "OPENAI_API_KEY=$OPENAI_API_KEY" \
  -w "$REMOTE_DIR" \
  "$CONTAINER" python3 eai_sda_runner_hard.py 2>&1 | tee "$run_log"

echo "==> Scoring the run for real (this gives the authoritative pass/fail per task) ..."
eval_log="$LOG_DIR/eval_hard50_r${BUDGET}.log"
docker exec -w "$REMOTE_DIR" "$CONTAINER" \
  python3 eval_hard50_one_budget.py "$BUDGET" "$MODEL_NAME" 2>&1 | tee "$eval_log"

echo "==> Pulling the run log and per-task results back to host ..."
docker cp "$CONTAINER:/opt/iGibson/results_hard50_budget_sweep/${MODEL_NAME}/error_info.json" \
  "$LOG_DIR/error_info_r${BUDGET}.json"
docker cp "$CONTAINER:/opt/iGibson/results_hard50_budget_sweep/${MODEL_NAME}/summary.json" \
  "$LOG_DIR/summary_r${BUDGET}.json"

echo ""
echo "==> Done. Budget=10 headline:"
cat "$LOG_DIR/summary_r${BUDGET}.json"
echo ""
echo "==> Now run:  python3 analyze_budget_stabilization.py"

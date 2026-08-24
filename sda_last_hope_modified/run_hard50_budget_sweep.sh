#!/bin/bash
# run_hard50_budget_sweep.sh
# ============================
# Runs the hard-50 benchmark once per repair budget (1 through 10 by
# default), scoring EACH budget right after it finishes, so you get the
# stabilization curve building up live instead of waiting for all 10 to
# complete. Safe to Ctrl+C at any point and re-run later — already-scored
# budgets are skipped, and the underlying runner resumes mid-budget too.
#
# What "budget" means here: HARD_MAX_REPLAN, i.e. how many repair attempts
# (diagnose + search + retry) the SDA loop is allowed per failing task
# before it gives up on that task. This is the same knob used for the
# earlier budget=3 / budget=5 comparison — this sweeps it from 1 to 10.
#
# Usage:
#   ./run_hard50_budget_sweep.sh
#   BUDGETS="1 2 4 6 8 10" ./run_hard50_budget_sweep.sh   # coarser/cheaper sweep
#
# Requirements: container already has OPENAI_API_KEY set in its env (same
# as every prior run in this project — nothing new to configure there).

set -euo pipefail

CONTAINER="bd0e40ed487c"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/opt/iGibson/sda_eai"
LOG_DIR="$LOCAL_DIR/run_fixed2_results/hard50_budget_sweep"
CSV_LOCAL="$LOG_DIR/budget_sweep_summary.csv"
BUDGETS="${BUDGETS:-1 2 3 4 5 6 7 8 9 10}"
MODEL="${HARD_MODEL:-gpt-4o-mini}"

mkdir -p "$LOG_DIR"

echo "==> Deploying current sda_last_hope_modified/ (incl. eval_hard50_one_budget.py) into container..."
docker exec "$CONTAINER" mkdir -p "$REMOTE_DIR"
docker cp "$LOCAL_DIR/." "$CONTAINER:$REMOTE_DIR"

echo "==> Sweeping budgets: $BUDGETS"
echo "budget,model_name,task_success_rate,state_goal,relation_goal,total_goal,execution_success_rate" > /tmp/sweep_header.csv

for budget in $BUDGETS; do
  model_name="${MODEL}-sda-tree_hard50_r${budget}"

  # Resume-friendly: skip budgets we've already scored in a previous
  # invocation of this script.
  if [ -f "$CSV_LOCAL" ] && grep -q "^${budget},${model_name}," "$CSV_LOCAL"; then
    echo "==> Budget $budget already scored, skipping (found in $CSV_LOCAL)"
    continue
  fi

  echo ""
  echo "==================================================================="
  echo "==> Budget $budget  (tag: $model_name)"
  echo "==================================================================="

  run_log="$LOG_DIR/run_hard50_r${budget}.log"
  echo "    Running SDA on all 50 hard tasks, repair budget=$budget ..."
  docker exec \
    -e "HARD_MODEL=$MODEL" \
    -e "HARD_MAX_REPLAN=$budget" \
    -w "$REMOTE_DIR" \
    "$CONTAINER" python3 eai_sda_runner_hard.py 2>&1 | tee "$run_log"

  echo "    Scoring budget=$budget ..."
  eval_log="$LOG_DIR/eval_hard50_r${budget}.log"
  docker exec -w "$REMOTE_DIR" "$CONTAINER" \
    python3 eval_hard50_one_budget.py "$budget" "$model_name" 2>&1 | tee "$eval_log"

  echo "    Pulling results CSV back to host ..."
  docker cp "$CONTAINER:/opt/iGibson/results_hard50_budget_sweep/budget_sweep_summary.csv" "$CSV_LOCAL"
done

echo ""
echo "==================================================================="
echo "==> Sweep complete. Full curve:"
echo "==================================================================="
column -s, -t "$CSV_LOCAL"

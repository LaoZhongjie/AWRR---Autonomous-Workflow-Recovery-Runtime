#!/usr/bin/env bash
set -euo pipefail

SEED=${SEED:-42}
N_TASKS=${N_TASKS:-100}

STAMP=$(date +"%Y%m%d_%H%M%S")
OUT_DIR="results/${STAMP}"
mkdir -p "$OUT_DIR"

TASKS="${OUT_DIR}/tasks.jsonl"
MEMORY="${OUT_DIR}/memory_bank.json"
LOG="${OUT_DIR}/run.log"

echo "============================================" | tee -a "$LOG"
echo "Baseline Benchmark (B1-B4)" | tee -a "$LOG"
echo "SEED=${SEED} N_TASKS=${N_TASKS}" | tee -a "$LOG"
echo "OUT_DIR=${OUT_DIR}" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"

echo "[1/3] Generate tasks (shuffled)" | tee -a "$LOG"
python task_generator.py --n "$N_TASKS" --seed "$SEED" --out "$TASKS" | tee -a "$LOG"

echo "[2/3] Run baselines (B1-B4)" | tee -a "$LOG"
for MODE in B1 B2 B3 B4; do
  OUT_TRACES="${OUT_DIR}/traces_${MODE}.jsonl"
  if [[ "$MODE" == "B4" ]]; then
    python baselines.py --tasks "$TASKS" --mode "$MODE" --seed "$SEED" --out "$OUT_TRACES" --memory "$MEMORY" | tee -a "$LOG"
  else
    python baselines.py --tasks "$TASKS" --mode "$MODE" --seed "$SEED" --out "$OUT_TRACES" | tee -a "$LOG"
  fi
done

echo "[3/3] Leaderboard (B1-B4)" | tee -a "$LOG"
python leaderboard.py --traces \
  "${OUT_DIR}/traces_B1.jsonl" \
  "${OUT_DIR}/traces_B2.jsonl" \
  "${OUT_DIR}/traces_B3.jsonl" \
  "${OUT_DIR}/traces_B4.jsonl" | tee "${OUT_DIR}/leaderboard.txt"

echo "[Extra] Visualize metrics" | tee -a "$LOG"
python visualize_metrics.py --results "$OUT_DIR" | tee -a "$LOG"

echo "Done. Results saved to ${OUT_DIR}" | tee -a "$LOG"

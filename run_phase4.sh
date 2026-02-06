#!/usr/bin/env bash
set -euo pipefail

# =========================
# AWRR Phase4 One-Click Runner
# =========================

PYTHON_BIN="${PYTHON_BIN:-python}"
SEED="${SEED:-42}"
N_TASKS="${N_TASKS:-100}"
BATCH_SIZE="${BATCH_SIZE:-10}"
OUT_DIR="${OUT_DIR:-runs/phase4_$(date +%Y%m%d_%H%M%S)}"

TASKS_FILE="$OUT_DIR/tasks.jsonl"
MEMORY_FILE="$OUT_DIR/memory_bank.json"

TR_B3="$OUT_DIR/traces_B3.jsonl"
TR_B4="$OUT_DIR/traces_B4.jsonl"
TR_B4_LEARN="$OUT_DIR/traces_B4_learning.jsonl"

LEARNING_HISTORY="$OUT_DIR/learning_curve.json"
LEARNING_PNG="$OUT_DIR/learning_curve.png"
BYPASS_TXT="$OUT_DIR/bypass_cases.txt"

LOG_FILE="$OUT_DIR/run.log"

mkdir -p "$OUT_DIR"

echo "============================================" | tee -a "$LOG_FILE"
echo "AWRR Phase4 One-Click Run" | tee -a "$LOG_FILE"
echo "OUT_DIR=$OUT_DIR" | tee -a "$LOG_FILE"
echo "PYTHON_BIN=$PYTHON_BIN SEED=$SEED N_TASKS=$N_TASKS BATCH_SIZE=$BATCH_SIZE" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"

# Helpful: fail early if required scripts are missing
REQUIRED_FILES=(task_generator.py baselines.py leaderboard.py learning_eval.py plot_learning.py extract_bypass_cases.py)
for f in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: Required file not found: $f (run this script at repo root)" | tee -a "$LOG_FILE"
    exit 1
  fi
done

echo "" | tee -a "$LOG_FILE"
echo "[1/7] Generate tasks" | tee -a "$LOG_FILE"
$PYTHON_BIN task_generator.py --n "$N_TASKS" --seed "$SEED" --out "$TASKS_FILE" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[2/7] Run B3 (Phase3 baseline)" | tee -a "$LOG_FILE"
$PYTHON_BIN baselines.py --tasks "$TASKS_FILE" --mode B3 --seed "$SEED" --out "$TR_B3" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[3/7] Run B4 (MemoryBank+Diagnosis) with persistence" | tee -a "$LOG_FILE"
# Note: We store memory in OUT_DIR to avoid clobbering repo root.
$PYTHON_BIN baselines.py --tasks "$TASKS_FILE" --mode B4 --seed "$SEED" --out "$TR_B4" --memory "$MEMORY_FILE" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[4/7] Learning eval (batch learning curve + earliest RR>=0.8 batch)" | tee -a "$LOG_FILE"
$PYTHON_BIN learning_eval.py \
  --tasks "$TASKS_FILE" \
  --seed "$SEED" \
  --batch-size "$BATCH_SIZE" \
  --memory "$MEMORY_FILE" \
  --out-history "$LEARNING_HISTORY" \
  --out-traces "$TR_B4_LEARN" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[5/7] Leaderboard (B3 vs B4)" | tee -a "$LOG_FILE"
$PYTHON_BIN leaderboard.py --traces "$TR_B3" "$TR_B4" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[6/7] Plot learning curve" | tee -a "$LOG_FILE"
$PYTHON_BIN plot_learning.py --history "$LEARNING_HISTORY" --out "$LEARNING_PNG" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "[7/7] Extract memory bypass cases" | tee -a "$LOG_FILE"
$PYTHON_BIN extract_bypass_cases.py --traces "$TR_B4" --k 5 --out "$BYPASS_TXT" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "✅ Done." | tee -a "$LOG_FILE"
echo "Artifacts saved to: $OUT_DIR" | tee -a "$LOG_FILE"
echo " - tasks:              $TASKS_FILE" | tee -a "$LOG_FILE"
echo " - memory:             $MEMORY_FILE" | tee -a "$LOG_FILE"
echo " - traces (B3):         $TR_B3" | tee -a "$LOG_FILE"
echo " - traces (B4):         $TR_B4" | tee -a "$LOG_FILE"
echo " - traces (B4 learn):   $TR_B4_LEARN" | tee -a "$LOG_FILE"
echo " - learning history:    $LEARNING_HISTORY" | tee -a "$LOG_FILE"
echo " - learning plot:       $LEARNING_PNG" | tee -a "$LOG_FILE"
echo " - bypass cases:        $BYPASS_TXT" | tee -a "$LOG_FILE"
echo " - full log:            $LOG_FILE" | tee -a "$LOG_FILE"

#!/bin/bash

echo "======================================================================"
echo "AWRR Phase 2: Diagnosis Agent + RCA Evaluation"
echo "======================================================================"
echo ""

# 确保任务已生成
if [ ! -f "tasks.jsonl" ]; then
    echo "[Setup] Generating tasks..."
    python task_generator.py
    echo ""
fi

# 1. 运行 B2 (baseline)
echo "[Step 1/4] Running B2 (Rule-Based)..."
python baselines.py --tasks tasks.jsonl --mode B2 --seed 42
echo ""

# 2. 运行 B3 (Diagnosis-driven)
echo "[Step 2/4] Running B3 (Diagnosis-driven)..."
python baselines.py --tasks tasks.jsonl --mode B3 --seed 42 --diagnosis-mode mock
echo ""

# 3. RCA 评估 (event-level)
echo "[Step 3/4] Evaluating RCA for B3 (event-level)..."
python rca_eval.py --traces traces_B3.jsonl --level event
echo ""

# 4. 完整 Leaderboard (B0-B3)
echo "[Step 4/4] Generating Complete Leaderboard (B0-B3)..."
python leaderboard.py --b0 traces_B0.jsonl --b1 traces_B1.jsonl --b2 traces_B2.jsonl --b3 traces_B3.jsonl

echo ""
echo "======================================================================"
echo "Phase 2 Complete!"
echo "======================================================================"
echo "Generated files:"
echo "  - traces_B2.jsonl"
echo "  - traces_B3.jsonl"
echo "  - leaderboard.csv"
echo ""
echo "Next: Review B2 vs B3 comparison above and proceed to Phase 3"
echo ""
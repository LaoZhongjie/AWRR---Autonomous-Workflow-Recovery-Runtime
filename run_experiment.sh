#!/bin/bash

echo "======================================================================"
echo "AWRR Phase 1: Baseline Experiment"
echo "======================================================================"
echo ""

# 1. 生成任务（50个，seed=42）
echo "[Step 1/5] Generating 50 tasks with seed=42..."
python task_generator.py
echo ""

# 2. 运行 B0
echo "[Step 2/5] Running B0 (No-Recovery)..."
python baselines.py --tasks tasks.jsonl --mode B0 --seed 42
echo ""

# 3. 运行 B1
echo "[Step 3/5] Running B1 (Naive-Retry)..."
python baselines.py --tasks tasks.jsonl --mode B1 --seed 42
echo ""

# 4. 运行 B2
echo "[Step 4/5] Running B2 (Rule-Based)..."
python baselines.py --tasks tasks.jsonl --mode B2 --seed 42
echo ""

# 5. 生成 Leaderboard
echo "[Step 5/5] Generating Leaderboard..."
python leaderboard.py --b0 traces_B0.jsonl --b1 traces_B1.jsonl --b2 traces_B2.jsonl
echo ""

echo "======================================================================"
echo "Experiment Complete!"
echo "======================================================================"
echo "Generated files:"
echo "  - tasks.jsonl"
echo "  - traces_B0.jsonl"
echo "  - traces_B1.jsonl"
echo "  - traces_B2.jsonl"
echo "  - leaderboard.csv"
echo ""
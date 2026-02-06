#!/bin/bash
set -e

echo "=========================================="
echo "Phase 3: Generating tasks.jsonl ..."
echo "=========================================="

python task_generator.py

echo ""
echo "=========================================="
echo "Phase 3: Running No-Saga baseline..."
echo "=========================================="
echo "=========================================="
echo "Phase 3: Running No-Saga baseline..."
echo "=========================================="

python runner.py --tasks tasks.jsonl --out traces_no_saga.jsonl --no-saga

echo ""
echo "=========================================="
echo "Phase 3: Running Saga baseline..."
echo "=========================================="

python runner.py --tasks tasks.jsonl --out traces_saga.jsonl --saga

echo ""
echo "=========================================="
echo "Phase 3: Evaluating SRR / RR / MTTR / RCO..."
echo "=========================================="

python phase3_eval.py \
  --no-saga traces_no_saga.jsonl \
  --saga traces_saga.jsonl

echo ""
echo "=========================================="
echo "Phase 3 Complete!"
echo "Generated:"
echo "  - traces_no_saga.jsonl"
echo "  - traces_saga.jsonl"
echo "=========================================="

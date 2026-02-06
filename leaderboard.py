import argparse
import os
import re
from metrics import compute_metrics

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None


def _infer_label(path: str) -> str:
    base = os.path.basename(path)
    match = re.search(r"B\d+", base)
    return match.group(0) if match else base


def generate_leaderboard(traces_paths: dict) -> tuple:
    """
    生成 baseline 对比 leaderboard
    
    Args:
        traces_paths: {"B0": "traces_B0.jsonl", "B1": "traces_B1.jsonl", ...}
    """
    
    results = []
    
    for baseline, traces_path in sorted(traces_paths.items()):
        print(f"Computing metrics for {baseline}...")
        try:
            metrics = compute_metrics(traces_path, baseline)
            results.append(metrics)
        except FileNotFoundError:
            print(f"  Warning: {traces_path} not found, skipping {baseline}")
        except Exception as e:
            print(f"  Error computing {baseline}: {e}")
    
    if not results:
        print("No valid results found")
        return [], []
    
    llm_calls_map = {item["baseline"]: item.get("llm_calls", 0) for item in results}
    b3_calls = llm_calls_map.get("B3", 0)
    llm_reduction_map = {}
    for item in results:
        baseline = item["baseline"]
        if baseline == "B4" and b3_calls:
            llm_reduction_map[baseline] = (b3_calls - llm_calls_map.get("B4", 0)) / b3_calls
        else:
            llm_reduction_map[baseline] = 0.0

    if pd is None:
        return results, results

    # 转换为 DataFrame
    df_raw = pd.DataFrame(results)

    # 选择展示列并格式化 (Phase4)
    display_df = pd.DataFrame(
        {
            "Strategy": df_raw["baseline"],
            "RR": df_raw["rr_task"].apply(lambda x: f"{x:.2%}"),
            "MTTR (ms)": df_raw["mttr_event"].apply(lambda x: f"{x:.1f}"),
            "RCO": df_raw["rco"].apply(lambda x: f"{x:.2%}"),
            "LLM_Calls": df_raw["llm_calls"].apply(lambda x: f"{int(x)}"),
            "LLM_Reduction": df_raw["baseline"].apply(
                lambda b: f"{llm_reduction_map.get(b, 0.0):.2%}" if b == "B4" and b3_calls else "-"
            ),
        }
    )

    return display_df, df_raw


def print_analysis(df_raw):
    """打印对比分析 - 重点 B2 vs B3"""
    print("\n" + "="*80)
    print("COMPARATIVE ANALYSIS")
    print("="*80)
    
    # 主分析：B2 vs B3
    def _get_row(label):
        if pd is None:
            return next(item for item in df_raw if item["baseline"] == label)
        return df_raw[df_raw["baseline"] == label].iloc[0]

    baselines = [row["baseline"] for row in df_raw] if pd is None else df_raw["baseline"].values

    if "B3" in baselines and "B4" in baselines:
        b3 = _get_row("B3")
        b4 = _get_row("B4")
        b3_calls = b3.get("llm_calls", 0)
        b4_calls = b4.get("llm_calls", 0)
        llm_reduction = ((b3_calls - b4_calls) / b3_calls) if b3_calls else 0.0

        print(f"\n{'='*80}")
        print("PRIMARY COMPARISON: B4 (Memory+Diagnosis) vs B3 (Diagnosis)")
        print(f"{'='*80}")
        print(f"RR_task: B3={b3['rr_task']:.1%}  B4={b4['rr_task']:.1%}")
        print(f"MTTR:    B3={b3['mttr_event']:.1f} ms  B4={b4['mttr_event']:.1f} ms")
        print(f"RCO:     B3={b3['rco']:.1%}  B4={b4['rco']:.1%}")
        print(f"LLM Calls: B3={b3_calls}  B4={b4_calls}  Reduction={llm_reduction:.1%}")
        if b4.get("preventive_predictions", 0):
            print(
                "Preventive Win Rate:"
                f" {b4.get('preventive_win_rate', 0.0):.1%}"
                f" ({b4.get('preventive_prevented', 0)}/{b4.get('preventive_predictions', 0)})"
            )

    if "B2" in baselines and "B3" in baselines:
        b2 = _get_row("B2")
        b3 = _get_row("B3")
        
        print(f"\n{'='*80}")
        print(f"PRIMARY COMPARISON: B3 (Diagnosis-driven) vs B2 (Rule-based)")
        print(f"{'='*80}")
        
        wcr_delta = (b3["wcr"] - b2["wcr"]) * 100
        rr_task_delta = (b3["rr_task"] - b2["rr_task"]) * 100
        rr_event_delta = (b3["rr_event"] - b2["rr_event"]) * 100
        mttr_delta = ((b3["mttr_event"] - b2["mttr_event"]) / b2["mttr_event"] * 100) if b2["mttr_event"] > 0 else 0
        rco_delta = (b3["rco"] - b2["rco"]) * 100
        hir_delta = (b3["hir"] - b2["hir"]) * 100
        
        print(f"\nWorkflow Completion Rate (WCR):")
        print(f"  B2: {b2['wcr']:.1%}  ({b2['completed']}/{b2['total_tasks']})")
        print(f"  B3: {b3['wcr']:.1%}  ({b3['completed']}/{b3['total_tasks']})")
        print(f"  Δ:  {wcr_delta:+.1f} pp {'✓ BETTER' if wcr_delta > 0 else '✗ WORSE' if wcr_delta < 0 else '= SAME'}")
        
        print(f"\nRecovery Rate (RR_task):")
        print(f"  B2: {b2['rr_task']:.1%}")
        print(f"  B3: {b3['rr_task']:.1%}")
        print(
            f"  Δ:  {rr_task_delta:+.1f} pp "
            f"{'✓ BETTER' if rr_task_delta > 0 else '✗ WORSE' if rr_task_delta < 0 else '= SAME'}"
        )

        print(f"\nRecovery Rate (RR_event):")
        print(f"  B2: {b2['rr_event']:.1%}")
        print(f"  B3: {b3['rr_event']:.1%}")
        print(
            f"  Δ:  {rr_event_delta:+.1f} pp "
            f"{'✓ BETTER' if rr_event_delta > 0 else '✗ WORSE' if rr_event_delta < 0 else '= SAME'}"
        )
        
        print(f"\nMean Time To Recovery (MTTR):")
        print(f"  B2: {b2['mttr_event']:.1f} ms")
        print(f"  B3: {b3['mttr_event']:.1f} ms")
        if b2["mttr_event"] > 0:
            print(f"  Δ:  {mttr_delta:+.1f}% {'✓ FASTER' if mttr_delta < 0 else '✗ SLOWER' if mttr_delta > 0 else '= SAME'}")
        
        print(f"\nRecovery Cost Overhead (RCO):")
        print(f"  B2: {b2['rco']:.1%}  (+{b2['actual_calls']-b2['baseline_calls']} calls)")
        print(f"  B3: {b3['rco']:.1%}  (+{b3['actual_calls']-b3['baseline_calls']} calls)")
        print(f"  Δ:  {rco_delta:+.1f} pp {'✓ CHEAPER' if rco_delta < 0 else '✗ COSTLIER' if rco_delta > 0 else '= SAME'}")
        
        print(f"\nHuman Intervention Rate (HIR):")
        print(f"  B2: {b2['hir']:.1%}  ({b2['escalated']}/{b2['total_tasks']})")
        print(f"  B3: {b3['hir']:.1%}  ({b3['escalated']}/{b3['total_tasks']})")
        print(f"  Δ:  {hir_delta:+.1f} pp {'✓ LESS' if hir_delta < 0 else '✗ MORE' if hir_delta > 0 else '= SAME'}")
        
        print(f"\n{'='*80}")
        print("KEY INSIGHTS:")
        print(f"{'='*80}")
        
        if wcr_delta > 5:
            print(f"  ✓ B3 significantly improves completion rate (+{wcr_delta:.1f} pp)")
        elif wcr_delta < -5:
            print(f"  ⚠ B3 degrades completion rate ({wcr_delta:.1f} pp) - review diagnosis logic")
        else:
            print(f"  • B3 has similar completion rate (Δ {wcr_delta:+.1f} pp)")
        
        if rco_delta < 0:
            print(f"  ✓ B3 reduces recovery cost ({rco_delta:.1f} pp)")
        elif rco_delta > 5:
            print(f"  ⚠ B3 increases recovery cost (+{rco_delta:.1f} pp)")
        
        if hir_delta > 10:
            print(f"  ⚠ B3 escalates much more often (+{hir_delta:.1f} pp) - possibly too conservative")
        
        print(f"{'='*80}\n")
    
    # 次要分析：B2 vs B1
    if "B1" in baselines and "B2" in baselines:
        b1 = _get_row("B1")
        b2 = _get_row("B2")
        
        print(f"\nSECONDARY: B2 vs B1 (Rule-based vs Naive-Retry)")
        print(
            f"  WCR: {b2['wcr']:.1%} vs {b1['wcr']:.1%}"
            f"  (Δ {(b2['wcr']-b1['wcr'])*100:+.1f} pp)"
        )
        print(
            f"  RCO: {b2['rco']:.1%} vs {b1['rco']:.1%}"
            f"  (Δ {(b2['rco']-b1['rco'])*100:+.1f} pp)"
        )
    
    # B0 基线
    if "B0" in baselines:
        b0 = _get_row("B0")
        print(f"\nB0 (No-Recovery) Baseline:")
        print(f"  WCR: {b0['wcr']:.1%} - lower bound without recovery")
    
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", nargs="*", default=None)
    parser.add_argument("--b0", default="traces_B0.jsonl")
    parser.add_argument("--b1", default="traces_B1.jsonl")
    parser.add_argument("--b2", default="traces_B2.jsonl")
    parser.add_argument("--b3", default="traces_B3.jsonl")
    parser.add_argument("--b4", default="traces_B4.jsonl")
    args = parser.parse_args()

    if args.traces:
        traces_paths = {_infer_label(path): path for path in args.traces}
    else:
        traces_paths = {
            "B0": args.b0,
            "B1": args.b1,
            "B2": args.b2,
            "B3": args.b3,
            "B4": args.b4,
        }
    
    print("\n" + "="*80)
    print("AWRR BASELINE LEADERBOARD (Phase 4)")
    print("="*80)
    
    display_df, raw_df = generate_leaderboard(traces_paths)
    
    if pd is None:
        has_results = bool(raw_df)
    else:
        has_results = not raw_df.empty

    if has_results:
        print("\n")
        if pd is None:
            b3_calls = 0
            for row in raw_df:
                if row["baseline"] == "B3":
                    b3_calls = row.get("llm_calls", 0)
                    break
            headers = ["Strategy", "RR", "MTTR(ms)", "RCO", "LLM_Calls", "LLM_Reduction"]
            print(" ".join(f"{h:>12s}" for h in headers))
            for row in raw_df:
                reduction = "-"
                if row["baseline"] == "B4" and b3_calls:
                    reduction = f"{(b3_calls - row.get('llm_calls', 0)) / b3_calls:.2%}"
                print(
                    f"{row['baseline']:>12s}"
                    f"{row['rr_task']:>12.2%}"
                    f"{row['mttr_event']:>12.1f}"
                    f"{row['rco']:>12.2%}"
                    f"{int(row.get('llm_calls', 0)):>12d}"
                    f"{reduction:>12s}"
                )
        else:
            print(display_df.to_string(index=False))
        print("\n")
        
        print_analysis(raw_df)
        
        # 保存为 CSV
        if pd is None:
            import csv
            with open("leaderboard.csv", "w", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=raw_df[0].keys())
                writer.writeheader()
                writer.writerows(raw_df)
        else:
            raw_df.to_csv("leaderboard.csv", index=False)
        print("Detailed results saved to: leaderboard.csv")
    else:
        print("No results to display")

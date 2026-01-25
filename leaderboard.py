import argparse
from metrics import compute_metrics
import pandas as pd


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
        return pd.DataFrame(), pd.DataFrame()
    
    # 转换为 DataFrame
    df_raw = pd.DataFrame(results)
    
    # 选择展示列并格式化
    display_df = pd.DataFrame({
        "Baseline": df_raw["baseline"],
        "WCR": df_raw["wcr"].apply(lambda x: f"{x:.2%}"),
        "RR": df_raw["rr"].apply(lambda x: f"{x:.2%}"),
        "MTTR (ms)": df_raw["mttr"].apply(lambda x: f"{x:.1f}"),
        "RCO": df_raw["rco"].apply(lambda x: f"{x:.2%}"),
        "HIR": df_raw["hir"].apply(lambda x: f"{x:.2%}"),
        "UAR": df_raw["uar"].apply(lambda x: f"{x:.2%}")
    })
    
    return display_df, df_raw


def print_analysis(df_raw: pd.DataFrame):
    """打印对比分析 - 重点 B2 vs B3"""
    print("\n" + "="*80)
    print("COMPARATIVE ANALYSIS")
    print("="*80)
    
    # 主分析：B2 vs B3
    if "B2" in df_raw["baseline"].values and "B3" in df_raw["baseline"].values:
        b2 = df_raw[df_raw["baseline"] == "B2"].iloc[0]
        b3 = df_raw[df_raw["baseline"] == "B3"].iloc[0]
        
        print(f"\n{'='*80}")
        print(f"PRIMARY COMPARISON: B3 (Diagnosis-driven) vs B2 (Rule-based)")
        print(f"{'='*80}")
        
        wcr_delta = (b3["wcr"] - b2["wcr"]) * 100
        rr_delta = (b3["rr"] - b2["rr"]) * 100
        mttr_delta = ((b3["mttr"] - b2["mttr"]) / b2["mttr"] * 100) if b2["mttr"] > 0 else 0
        rco_delta = (b3["rco"] - b2["rco"]) * 100
        hir_delta = (b3["hir"] - b2["hir"]) * 100
        
        print(f"\nWorkflow Completion Rate (WCR):")
        print(f"  B2: {b2['wcr']:.1%}  ({b2['completed']}/{b2['total_tasks']})")
        print(f"  B3: {b3['wcr']:.1%}  ({b3['completed']}/{b3['total_tasks']})")
        print(f"  Δ:  {wcr_delta:+.1f} pp {'✓ BETTER' if wcr_delta > 0 else '✗ WORSE' if wcr_delta < 0 else '= SAME'}")
        
        print(f"\nRecovery Rate (RR):")
        print(f"  B2: {b2['rr']:.1%}  ({b2['recovered']}/{b2['total_errors']})")
        print(f"  B3: {b3['rr']:.1%}  ({b3['recovered']}/{b3['total_errors']})")
        print(f"  Δ:  {rr_delta:+.1f} pp {'✓ BETTER' if rr_delta > 0 else '✗ WORSE' if rr_delta < 0 else '= SAME'}")
        
        print(f"\nMean Time To Recovery (MTTR):")
        print(f"  B2: {b2['mttr']:.1f} ms")
        print(f"  B3: {b3['mttr']:.1f} ms")
        if b2["mttr"] > 0:
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
    if "B1" in df_raw["baseline"].values and "B2" in df_raw["baseline"].values:
        b1 = df_raw[df_raw["baseline"] == "B1"].iloc[0]
        b2 = df_raw[df_raw["baseline"] == "B2"].iloc[0]
        
        print(f"\nSECONDARY: B2 vs B1 (Rule-based vs Naive-Retry)")
        print(f"  WCR: {b2['wcr']:.1%} vs {b1['wcr']:.1%}  (Δ {(b2['wcr']-b1['wcr'])*100:+.1f} pp)")
        print(f"  RCO: {b2['rco']:.1%} vs {b1['rco']:.1%}  (Δ {(b2['rco']-b1['rco'])*100:+.1f} pp)")
    
    # B0 基线
    if "B0" in df_raw["baseline"].values:
        b0 = df_raw[df_raw["baseline"] == "B0"].iloc[0]
        print(f"\nB0 (No-Recovery) Baseline:")
        print(f"  WCR: {b0['wcr']:.1%} - lower bound without recovery")
    
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--b0", default="traces_B0.jsonl")
    parser.add_argument("--b1", default="traces_B1.jsonl")
    parser.add_argument("--b2", default="traces_B2.jsonl")
    parser.add_argument("--b3", default="traces_B3.jsonl")
    args = parser.parse_args()
    
    traces_paths = {
        "B0": args.b0,
        "B1": args.b1,
        "B2": args.b2,
        "B3": args.b3
    }
    
    print("\n" + "="*80)
    print("AWRR BASELINE LEADERBOARD (Phase 2)")
    print("="*80)
    
    display_df, raw_df = generate_leaderboard(traces_paths)
    
    if not raw_df.empty:
        print("\n")
        print(display_df.to_string(index=False))
        print("\n")
        
        print_analysis(raw_df)
        
        # 保存为 CSV
        raw_df.to_csv("leaderboard.csv", index=False)
        print("Detailed results saved to: leaderboard.csv")
    else:
        print("No results to display")
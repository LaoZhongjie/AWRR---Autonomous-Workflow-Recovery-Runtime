import argparse
from metrics import compute_metrics
import pandas as pd


def generate_leaderboard(traces_paths: dict) -> pd.DataFrame:
    """
    生成 baseline 对比 leaderboard
    
    Args:
        traces_paths: {"B0": "traces_B0.jsonl", "B1": "traces_B1.jsonl", ...}
    """
    
    results = []
    
    for baseline, traces_path in traces_paths.items():
        print(f"Computing metrics for {baseline}...")
        metrics = compute_metrics(traces_path, baseline)
        results.append(metrics)
    
    # 转换为 DataFrame
    df = pd.DataFrame(results)
    
    # 选择展示列并格式化
    display_df = pd.DataFrame({
        "Baseline": df["baseline"],
        "WCR": df["wcr"].apply(lambda x: f"{x:.2%}"),
        "RR": df["rr"].apply(lambda x: f"{x:.2%}"),
        "MTTR (ms)": df["mttr"].apply(lambda x: f"{x:.1f}"),
        "RCO": df["rco"].apply(lambda x: f"{x:.2%}"),
        "HIR": df["hir"].apply(lambda x: f"{x:.2%}"),
        "UAR": df["uar"].apply(lambda x: f"{x:.2%}")
    })
    
    return display_df, df


def print_analysis(df_raw: pd.DataFrame):
    """打印对比分析"""
    print("\n" + "="*80)
    print("COMPARATIVE ANALYSIS")
    print("="*80)
    
    if "B1" in df_raw["baseline"].values and "B2" in df_raw["baseline"].values:
        b1 = df_raw[df_raw["baseline"] == "B1"].iloc[0]
        b2 = df_raw[df_raw["baseline"] == "B2"].iloc[0]
        
        wcr_improvement = (b2["wcr"] - b1["wcr"]) / b1["wcr"] * 100 if b1["wcr"] > 0 else 0
        rco_increase = (b2["rco"] - b1["rco"]) / b1["rco"] * 100 if b1["rco"] > 0 else 0
        
        print(f"\nB2 (Rule-Based) vs B1 (Naive-Retry):")
        print(f"  WCR Improvement:  {wcr_improvement:+.1f}%")
        print(f"  RR  Improvement:  {(b2['rr'] - b1['rr'])*100:+.1f} pp")
        print(f"  RCO Change:       {rco_increase:+.1f}%")
        print(f"  HIR Change:       {(b2['hir'] - b1['hir'])*100:+.1f} pp")
        print(f"\nKey Insights:")
        print(f"  - B2 completes {b2['wcr']:.1%} of tasks vs B1's {b1['wcr']:.1%}")
        print(f"  - B2 costs {b2['rco']:.1%} extra overhead vs B1's {b1['rco']:.1%}")
        print(f"  - B2 escalates {b2['hir']:.1%} of tasks vs B1's {b1['hir']:.1%}")
    
    if "B0" in df_raw["baseline"].values:
        b0 = df_raw[df_raw["baseline"] == "B0"].iloc[0]
        print(f"\nB0 (No-Recovery) Baseline:")
        print(f"  WCR: {b0['wcr']:.1%} (lower bound without any recovery)")
        print(f"  RCO: {b0['rco']:.1%} (minimal cost)")
    
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--b0", default="traces_B0.jsonl")
    parser.add_argument("--b1", default="traces_B1.jsonl")
    parser.add_argument("--b2", default="traces_B2.jsonl")
    args = parser.parse_args()
    
    traces_paths = {
        "B0": args.b0,
        "B1": args.b1,
        "B2": args.b2
    }
    
    print("\n" + "="*80)
    print("AWRR BASELINE LEADERBOARD")
    print("="*80)
    
    display_df, raw_df = generate_leaderboard(traces_paths)
    
    print("\n")
    print(display_df.to_string(index=False))
    print("\n")
    
    print_analysis(raw_df)
    
    # 保存为 CSV
    raw_df.to_csv("leaderboard.csv", index=False)
    print("Detailed results saved to: leaderboard.csv")
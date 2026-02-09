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


def _fmt_pp(delta: float) -> str:
    return f"{delta:+.1f}pp"

def _fmt_pct(x: float) -> str:
    return f"{x:.2%}"

def _fmt_float(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}"

def _fmt_int(x: int) -> str:
    return f"{int(x)}"

def _get_row(df_raw, label: str):
    if pd is None:
        return next((item for item in df_raw if item["baseline"] == label), None)
    hits = df_raw[df_raw["baseline"] == label]
    if hits.empty:
        return None
    return hits.iloc[0]

def _get_val(row, key: str, default=None):
    if row is None:
        return default
    if pd is None:
        return row.get(key, default)
    v = row.get(key, default)
    # pandas may store dicts as objects; leave as-is.
    return v if v is not None else default


def generate_leaderboard(traces_paths: dict, wide: bool = False, ref: str | None = None) -> tuple:
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

    ref_row = _get_row(df_raw, ref) if ref else None

    # Core columns (compact but comparable)
    display = {
        "Strategy": df_raw["baseline"],
        "WCR": df_raw["wcr"].apply(_fmt_pct),
        "RR_task": df_raw["rr_task"].apply(_fmt_pct),
        "HIR": df_raw["hir"].apply(_fmt_pct),
        "MTTR (ms)": df_raw["mttr_event"].apply(lambda x: _fmt_float(x, 1)),
        "RCO": df_raw["rco"].apply(_fmt_pct),
        "CPS": df_raw["cps"].apply(lambda x: _fmt_float(x, 2)),
        "LLM_Calls": df_raw["llm_calls"].apply(_fmt_int),
        "LLM_Reduction": df_raw["baseline"].apply(
            lambda b: _fmt_pct(llm_reduction_map.get(b, 0.0)) if b == "B4" and b3_calls else "-"
        ),
    }

    if wide:
        display.update(
            {
                "RR_event": df_raw["rr_event"].apply(_fmt_pct),
                "CPT": df_raw["cpt"].apply(lambda x: _fmt_float(x, 2)),
                "UAR": df_raw["uar"].apply(_fmt_pct),
                "SRR": df_raw["srr"].apply(_fmt_pct),
            }
        )

    if ref_row is not None:
        ref_wcr = float(_get_val(ref_row, "wcr", 0.0) or 0.0)
        ref_rr = float(_get_val(ref_row, "rr_task", 0.0) or 0.0)
        ref_hir = float(_get_val(ref_row, "hir", 0.0) or 0.0)
        ref_mttr = float(_get_val(ref_row, "mttr_event", 0.0) or 0.0)
        ref_rco = float(_get_val(ref_row, "rco", 0.0) or 0.0)
        ref_cps = float(_get_val(ref_row, "cps", 0.0) or 0.0)
        display.update(
            {
                f"ΔWCR vs {ref}": df_raw["wcr"].apply(lambda x: _fmt_pp((x - ref_wcr) * 100)),
                f"ΔRR vs {ref}": df_raw["rr_task"].apply(lambda x: _fmt_pp((x - ref_rr) * 100)),
                f"ΔHIR vs {ref}": df_raw["hir"].apply(lambda x: _fmt_pp((x - ref_hir) * 100)),
                f"ΔMTTR vs {ref}": df_raw["mttr_event"].apply(lambda x: f"{(x - ref_mttr):+.1f}"),
                f"ΔRCO vs {ref}": df_raw["rco"].apply(lambda x: _fmt_pp((x - ref_rco) * 100)),
                f"ΔCPS vs {ref}": df_raw["cps"].apply(lambda x: f"{(x - ref_cps):+.2f}"),
            }
        )

    display_df = pd.DataFrame(display)
    # More informative default ordering: best completion first, then recovery, then faster MTTR.
    try:
        display_df = display_df.iloc[
            df_raw.sort_values(["wcr", "rr_task", "mttr_event"], ascending=[False, False, True]).index
        ]
    except Exception:
        pass

    return display_df, df_raw


def print_analysis(df_raw, ref: str = "B2", details: bool = False):
    """打印对比分析 - 默认以 B2 为参考"""
    print("\n" + "="*80)
    print("COMPARATIVE ANALYSIS")
    print("="*80)
    
    # 主分析：B2 vs B3
    baselines = [row["baseline"] for row in df_raw] if pd is None else df_raw["baseline"].values

    if "B3" in baselines and "B4" in baselines:
        b3 = _get_row(df_raw, "B3")
        b4 = _get_row(df_raw, "B4")
        b3_calls = int(_get_val(b3, "llm_calls", 0) or 0)
        b4_calls = int(_get_val(b4, "llm_calls", 0) or 0)
        llm_reduction = ((b3_calls - b4_calls) / b3_calls) if b3_calls else 0.0

        print(f"\n{'='*80}")
        print("PRIMARY COMPARISON: B4 (Memory+Diagnosis) vs B3 (Diagnosis)")
        print(f"{'='*80}")
        print(f"RR_task: B3={_get_val(b3,'rr_task',0.0):.1%}  B4={_get_val(b4,'rr_task',0.0):.1%}")
        print(f"MTTR:    B3={_get_val(b3,'mttr_event',0.0):.1f} ms  B4={_get_val(b4,'mttr_event',0.0):.1f} ms")
        print(f"RCO:     B3={_get_val(b3,'rco',0.0):.1%}  B4={_get_val(b4,'rco',0.0):.1%}")
        print(f"LLM Calls: B3={b3_calls}  B4={b4_calls}  Reduction={llm_reduction:.1%}")

    if ref in baselines and "B3" in baselines:
        b2 = _get_row(df_raw, ref)
        b3 = _get_row(df_raw, "B3")
        
        print(f"\n{'='*80}")
        print(f"PRIMARY COMPARISON: B3 (Diagnosis-driven) vs {ref}")
        print(f"{'='*80}")
        
        wcr_delta = (_get_val(b3, "wcr", 0.0) - _get_val(b2, "wcr", 0.0)) * 100
        rr_task_delta = (_get_val(b3, "rr_task", 0.0) - _get_val(b2, "rr_task", 0.0)) * 100
        rr_event_delta = (_get_val(b3, "rr_event", 0.0) - _get_val(b2, "rr_event", 0.0)) * 100
        b2_mttr = float(_get_val(b2, "mttr_event", 0.0) or 0.0)
        b3_mttr = float(_get_val(b3, "mttr_event", 0.0) or 0.0)
        mttr_delta = ((b3_mttr - b2_mttr) / b2_mttr * 100) if b2_mttr > 0 else 0
        rco_delta = (_get_val(b3, "rco", 0.0) - _get_val(b2, "rco", 0.0)) * 100
        hir_delta = (_get_val(b3, "hir", 0.0) - _get_val(b2, "hir", 0.0)) * 100
        
        print(f"\nWorkflow Completion Rate (WCR):")
        print(f"  {ref}: {_get_val(b2,'wcr',0.0):.1%}  ({int(_get_val(b2,'completed',0))}/{int(_get_val(b2,'total_tasks',0))})")
        print(f"  B3: {_get_val(b3,'wcr',0.0):.1%}  ({int(_get_val(b3,'completed',0))}/{int(_get_val(b3,'total_tasks',0))})")
        print(f"  Δ:  {wcr_delta:+.1f} pp {'✓ BETTER' if wcr_delta > 0 else '✗ WORSE' if wcr_delta < 0 else '= SAME'}")
        
        print(f"\nRecovery Rate (RR_task):")
        print(f"  {ref}: {_get_val(b2,'rr_task',0.0):.1%}")
        print(f"  B3: {_get_val(b3,'rr_task',0.0):.1%}")
        print(
            f"  Δ:  {rr_task_delta:+.1f} pp "
            f"{'✓ BETTER' if rr_task_delta > 0 else '✗ WORSE' if rr_task_delta < 0 else '= SAME'}"
        )

        print(f"\nRecovery Rate (RR_event):")
        print(f"  {ref}: {_get_val(b2,'rr_event',0.0):.1%}")
        print(f"  B3: {_get_val(b3,'rr_event',0.0):.1%}")
        print(
            f"  Δ:  {rr_event_delta:+.1f} pp "
            f"{'✓ BETTER' if rr_event_delta > 0 else '✗ WORSE' if rr_event_delta < 0 else '= SAME'}"
        )
        
        print(f"\nMean Time To Recovery (MTTR):")
        print(f"  {ref}: {b2_mttr:.1f} ms")
        print(f"  B3: {b3_mttr:.1f} ms")
        if b2_mttr > 0:
            print(f"  Δ:  {mttr_delta:+.1f}% {'✓ FASTER' if mttr_delta < 0 else '✗ SLOWER' if mttr_delta > 0 else '= SAME'}")
        
        print(f"\nRecovery Cost Overhead (RCO):")
        b2_over = int(_get_val(b2, "actual_calls", 0) - _get_val(b2, "baseline_calls", 0))
        b3_over = int(_get_val(b3, "actual_calls", 0) - _get_val(b3, "baseline_calls", 0))
        print(f"  {ref}: {_get_val(b2,'rco',0.0):.1%}  (+{b2_over} calls)")
        print(f"  B3: {_get_val(b3,'rco',0.0):.1%}  (+{b3_over} calls)")
        print(f"  Δ:  {rco_delta:+.1f} pp {'✓ CHEAPER' if rco_delta < 0 else '✗ COSTLIER' if rco_delta > 0 else '= SAME'}")
        
        print(f"\nHuman Intervention Rate (HIR):")
        print(f"  {ref}: {_get_val(b2,'hir',0.0):.1%}  ({int(_get_val(b2,'escalated',0))}/{int(_get_val(b2,'total_tasks',0))})")
        print(f"  B3: {_get_val(b3,'hir',0.0):.1%}  ({int(_get_val(b3,'escalated',0))}/{int(_get_val(b3,'total_tasks',0))})")
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
        b1 = _get_row(df_raw, "B1")
        b2 = _get_row(df_raw, "B2")
        
        print(f"\nSECONDARY: B2 vs B1 (Rule-based vs Naive-Retry)")
        print(
            f"  WCR: {_get_val(b2,'wcr',0.0):.1%} vs {_get_val(b1,'wcr',0.0):.1%}"
            f"  (Δ {(_get_val(b2,'wcr',0.0)-_get_val(b1,'wcr',0.0))*100:+.1f} pp)"
        )
        print(
            f"  RCO: {_get_val(b2,'rco',0.0):.1%} vs {_get_val(b1,'rco',0.0):.1%}"
            f"  (Δ {(_get_val(b2,'rco',0.0)-_get_val(b1,'rco',0.0))*100:+.1f} pp)"
        )
    
    # B0 基线
    if "B0" in baselines:
        b0 = _get_row(df_raw, "B0")
        print(f"\nB0 (No-Recovery) Baseline:")
        print(f"  WCR: {_get_val(b0,'wcr',0.0):.1%} - lower bound without recovery")

    # Quick "best of" summary for scanning.
    try:
        if pd is not None:
            df = df_raw.copy()
            best = {
                "WCR": df.loc[df["wcr"].idxmax(), ["baseline", "wcr"]].to_dict(),
                "RR_task": df.loc[df["rr_task"].idxmax(), ["baseline", "rr_task"]].to_dict(),
                "HIR (lowest)": df.loc[df["hir"].idxmin(), ["baseline", "hir"]].to_dict(),
                "MTTR (lowest)": df.loc[df["mttr_event"].idxmin(), ["baseline", "mttr_event"]].to_dict(),
                "RCO (lowest)": df.loc[df["rco"].idxmin(), ["baseline", "rco"]].to_dict(),
                "CPS (lowest)": df.loc[df["cps"].idxmin(), ["baseline", "cps"]].to_dict(),
            }
            print("\n" + "="*80)
            print("BEST-OF SUMMARY")
            print("="*80)
            print(f"  WCR: {best['WCR']['baseline']}={best['WCR']['wcr']:.1%}")
            print(f"  RR_task: {best['RR_task']['baseline']}={best['RR_task']['rr_task']:.1%}")
            print(f"  HIR(low): {best['HIR (lowest)']['baseline']}={best['HIR (lowest)']['hir']:.1%}")
            print(f"  MTTR(low): {best['MTTR (lowest)']['baseline']}={best['MTTR (lowest)']['mttr_event']:.1f} ms")
            print(f"  RCO(low): {best['RCO (lowest)']['baseline']}={best['RCO (lowest)']['rco']:.1%}")
            print(f"  CPS(low): {best['CPS (lowest)']['baseline']}={best['CPS (lowest)']['cps']:.2f}")
        else:
            rows = list(df_raw)
            def _best_max(k): return max(rows, key=lambda r: r.get(k, 0.0))
            def _best_min(k): return min(rows, key=lambda r: r.get(k, 0.0))
            w = _best_max("wcr"); rr = _best_max("rr_task"); hir = _best_min("hir")
            mttr = _best_min("mttr_event"); rco = _best_min("rco"); cps = _best_min("cps")
            print("\n" + "="*80)
            print("BEST-OF SUMMARY")
            print("="*80)
            print(f"  WCR: {w['baseline']}={w.get('wcr',0.0):.1%}")
            print(f"  RR_task: {rr['baseline']}={rr.get('rr_task',0.0):.1%}")
            print(f"  HIR(low): {hir['baseline']}={hir.get('hir',0.0):.1%}")
            print(f"  MTTR(low): {mttr['baseline']}={mttr.get('mttr_event',0.0):.1f} ms")
            print(f"  RCO(low): {rco['baseline']}={rco.get('rco',0.0):.1%}")
            print(f"  CPS(low): {cps['baseline']}={cps.get('cps',0.0):.2f}")
    except Exception:
        pass

    if details:
        print("\n" + "="*80)
        print("ESCALATION BREAKDOWN (top reasons per strategy)")
        print("="*80)
        for b in baselines:
            row = _get_row(df_raw, b) if pd is not None else next((r for r in df_raw if r["baseline"] == b), None)
            reasons = _get_val(row, "final_reason_counts", {}) or {}
            if not isinstance(reasons, dict):
                reasons = {}
            top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            top_str = ", ".join(f"{k}={v}" for k, v in top) if top else "-"
            print(f"  {b}: {top_str}")
    
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", nargs="*", default=None)
    parser.add_argument("--b0", default="traces_B0.jsonl")
    parser.add_argument("--b1", default="traces_B1.jsonl")
    parser.add_argument("--b2", default="traces_B2.jsonl")
    parser.add_argument("--b3", default="traces_B3.jsonl")
    parser.add_argument("--b4", default="traces_B4.jsonl")
    parser.add_argument("--wide", action="store_true", help="Show more columns")
    parser.add_argument("--ref", default=None, help="Reference baseline for Δ columns (e.g., B2)")
    parser.add_argument("--details", action="store_true", help="Print escalation breakdown")
    parser.add_argument("--csv", default="leaderboard.csv", help="CSV output path")
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
    
    display_df, raw_df = generate_leaderboard(traces_paths, wide=args.wide, ref=args.ref)
    
    if pd is None:
        has_results = bool(raw_df)
    else:
        has_results = not raw_df.empty

    if has_results:
        print("\n")
        if pd is None:
            # Minimal table when pandas is unavailable.
            headers = ["Strategy", "WCR", "RR_task", "HIR", "MTTR(ms)", "RCO", "CPS", "LLM_Calls"]
            print(" ".join(f"{h:>12s}" for h in headers))
            for row in raw_df:
                print(
                    f"{row['baseline']:>12s}"
                    f"{row.get('wcr', 0.0):>12.2%}"
                    f"{row.get('rr_task', 0.0):>12.2%}"
                    f"{row.get('hir', 0.0):>12.2%}"
                    f"{row.get('mttr_event', 0.0):>12.1f}"
                    f"{row.get('rco', 0.0):>12.2%}"
                    f"{row.get('cps', 0.0):>12.2f}"
                    f"{int(row.get('llm_calls', 0)):>12d}"
                )
        else:
            print(display_df.to_string(index=False))
        print("\n")
        
        print_analysis(raw_df, ref=args.ref or "B2", details=args.details)
        
        # 保存为 CSV
        if pd is None:
            import csv
            scalar_rows = []
            for r in raw_df:
                scalar_rows.append(r.get("summary") or r)
            with open(args.csv, "w", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=scalar_rows[0].keys())
                writer.writeheader()
                writer.writerows(scalar_rows)
        else:
            # Drop nested objects for CSV readability
            summary_rows = []
            for _, row in raw_df.iterrows():
                summary_rows.append(row.get("summary") or row.to_dict())
            pd.DataFrame(summary_rows).to_csv(args.csv, index=False)
        print(f"Detailed results saved to: {args.csv}")
    else:
        print("No results to display")

import argparse
import json
import os
from typing import Dict, List

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

from metrics import compute_metrics


def load_metrics(traces_dir: str) -> List[Dict]:
    metrics_list = []
    for fname in sorted(os.listdir(traces_dir)):
        if not fname.startswith("traces_") or not fname.endswith(".jsonl"):
            continue
        baseline = fname.split("_")[1].split(".")[0]  # e.g., traces_B1.jsonl -> B1
        path = os.path.join(traces_dir, fname)
        metrics_list.append(compute_metrics(path, baseline))
    return sorted(metrics_list, key=lambda m: m["baseline"])


def _bar(ax, labels, values, title, ylabel=None):
    ax.bar(labels, values, color="#4C78A8")
    ax.set_title(title)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.set_ylim(bottom=0)


def plot_metrics(metrics_list: List[Dict], out_dir: str):
    if plt is None:
        print("matplotlib not installed; skipping plot generation")
        with open(os.path.join(out_dir, "metrics_summary.json"), "w") as f:
            json.dump(metrics_list, f, indent=2)
        return

    baselines = [m["baseline"] for m in metrics_list]

    # Rates
    rate_keys = [
        ("wcr", "WCR"),
        ("rr_task", "RR_task"),
        ("rr_event", "RR_event"),
        ("hir", "HIR"),
        ("uar", "UAR"),
        ("rco", "RCO"),
    ]
    if any(m.get("srr_eligible", 0) for m in metrics_list):
        rate_keys.append(("srr", "SRR"))

    fig_rates, axs_rates = plt.subplots(1, len(rate_keys), figsize=(3 * len(rate_keys), 4), constrained_layout=True)
    if len(rate_keys) == 1:
        axs_rates = [axs_rates]
    for ax, (k, label) in zip(axs_rates, rate_keys):
        vals = [m.get(k, 0) * 100 for m in metrics_list]
        _bar(ax, baselines, vals, label, ylabel="Percent")
    rates_path = os.path.join(out_dir, "metrics_rates.png")
    fig_rates.suptitle("Rate Metrics (%)", fontsize=12)
    fig_rates.savefig(rates_path, dpi=160)
    plt.close(fig_rates)

    # Cost / latency / calls
    cost_items = [
        ("mttr_event", "MTTR_event (ms)", 1.0),
        ("cpt", "CPT (calls/task)", 1.0),
        ("cps", "CPS (calls/success)", 1.0),
        ("tool_calls_total", "Tool Calls Total", 1.0),
        ("llm_calls", "LLM Calls", 1.0),
    ]
    fig_cost, axs_cost = plt.subplots(1, len(cost_items), figsize=(3 * len(cost_items), 4), constrained_layout=True)
    if len(cost_items) == 1:
        axs_cost = [axs_cost]
    for ax, (k, label, scale) in zip(axs_cost, cost_items):
        vals = [m.get(k, 0) * scale for m in metrics_list]
        _bar(ax, baselines, vals, label)
    cost_path = os.path.join(out_dir, "metrics_costs.png")
    fig_cost.suptitle("Cost / Latency / Calls", fontsize=12)
    fig_cost.savefig(cost_path, dpi=160)
    plt.close(fig_cost)

    # Dump raw metrics for traceability
    with open(os.path.join(out_dir, "metrics_summary.json"), "w") as f:
        json.dump(metrics_list, f, indent=2)

    print(f"Saved {rates_path}")
    print(f"Saved {cost_path}")
    print(f"Saved metrics_summary.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, help="Path to a results/<timestamp> directory")
    args = parser.parse_args()

    metrics_list = load_metrics(args.results)
    if not metrics_list:
        raise SystemExit("No traces_*.jsonl found in results directory")

    plot_metrics(metrics_list, args.results)


if __name__ == "__main__":
    main()

import argparse
import json

import matplotlib.pyplot as plt


def plot_learning_curve(history_path: str, out_path: str):
    with open(history_path, "r") as f:
        history = json.load(f)

    if not history:
        raise ValueError("Empty learning curve history")

    episodes = [item["episode"] for item in history]
    rr_values = [item.get("rr_cumulative", 0.0) for item in history]

    plt.figure(figsize=(8, 4))
    plt.plot(episodes, rr_values, marker="o", linewidth=2)
    plt.title("Learning Curve (RR vs Episode)")
    plt.xlabel("Episode")
    plt.ylabel("RR (cumulative)")
    plt.ylim(0, 1.0)
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    print(f"Saved learning curve to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", default="learning_curve.json")
    parser.add_argument("--out", default="learning_curve.png")
    args = parser.parse_args()

    plot_learning_curve(args.history, args.out)

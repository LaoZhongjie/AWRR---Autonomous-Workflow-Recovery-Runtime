import argparse
from metrics import compute_metrics


def _format_row(label: str, metrics: dict) -> str:
    return (
        f"| {label} | {metrics['srr']:.2%} | {metrics['rr_task']:.2%} | "
        f"{metrics['mttr_event']:.1f} ms | {metrics['rco']:.2%} |"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-saga", required=True, help="traces for no-saga run")
    parser.add_argument("--saga", required=True, help="traces for saga run")
    args = parser.parse_args()

    no_saga_metrics = compute_metrics(args.no_saga, baseline_name="no-saga")
    saga_metrics = compute_metrics(args.saga, baseline_name="saga")

    print("\n| Strategy | SRR | RR | MTTR | RCO |")
    print("| --- | --- | --- | --- | --- |")
    print(_format_row("No Saga", no_saga_metrics))
    print(_format_row("Saga", saga_metrics))
    print("")
    print("SRR eligible samples (need compensation):")
    print(f"  No Saga: {no_saga_metrics.get('srr_eligible', 0)} (pass {no_saga_metrics.get('srr_pass', 0)})")
    print(f"  Saga   : {saga_metrics.get('srr_eligible', 0)} (pass {saga_metrics.get('srr_pass', 0)})")


if __name__ == "__main__":
    main()

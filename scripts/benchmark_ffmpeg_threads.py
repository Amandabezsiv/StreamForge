"""Run the Experiment 005 worker/thread allocation matrix."""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CONFIGURATIONS = (
    {"name": "2-workers-auto", "workers": 2, "threads": "auto"},
    {"name": "2-workers-4-threads", "workers": 2, "threads": "4"},
    {"name": "3-workers-4-threads", "workers": 3, "threads": "4"},
    {"name": "4-workers-3-threads", "workers": 4, "threads": "3"},
)
EXPERIMENT_DIR = Path("experiments/005-ffmpeg-thread-allocation")


def concise_result(result: dict) -> dict:
    resources = result["resources"]
    return {
        "configuration": result["configuration"],
        "batch": result["batch"],
        "queue_wait": result["summary_seconds"]["queue_wait_time"],
        "processing_duration": result["summary_seconds"]["processing_duration"],
        "total_time_to_ready": result["summary_seconds"]["total_time_to_ready"],
        "cpu_percent_workers_total": resources["cpu_percent_workers_total"],
        "memory_mib_workers_total": resources["memory_mib_workers_total"],
        "block_io_bytes_workers_total": resources["block_io_bytes_workers_total"],
        "errors": result["batch"]["failed"],
        "duplicate_processing": result["duplicate_processing"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    for position, configuration in enumerate(CONFIGURATIONS, start=1):
        output = EXPERIMENT_DIR / f"results-{configuration['name']}.json"
        print(
            f"[{position}/{len(CONFIGURATIONS)}] {configuration['name']}",
            flush=True,
        )
        subprocess.run(
            [
                sys.executable,
                "scripts/benchmark_multi_worker.py",
                "--workers",
                str(configuration["workers"]),
                "--ffmpeg-threads",
                configuration["threads"],
                "--concurrency",
                str(args.concurrency),
                "--timeout",
                str(args.timeout),
                "--output",
                str(output),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        result = json.loads(output.read_text(encoding="utf-8"))
        summaries.append(concise_result(result))

    report = {
        "benchmark": "005-ffmpeg-thread-allocation",
        "recorded_at": datetime.now(UTC).isoformat(),
        "configuration_count": len(CONFIGURATIONS),
        "concurrent_uploads_per_configuration": args.concurrency,
        "results": summaries,
    }
    summary_path = EXPERIMENT_DIR / "results-summary.json"
    summary_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

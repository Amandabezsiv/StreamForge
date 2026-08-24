"""Run repeated StreamForge benchmarks and calculate latency distributions."""

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from benchmark_e2e import generate_fixture, run_benchmark
from benchmark_sizes import FIXTURES

METRICS = (
    "queue_wait_time",
    "processing_duration",
    "transcoding_duration",
    "total_time_to_ready",
)


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "p50": nearest_rank_percentile(values, 0.50),
        "p95": nearest_rank_percentile(values, 0.95),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/001-single-worker-baseline/results-repeated-20.json"
        ),
    )
    parser.add_argument("--regenerate-fixtures", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    profiles = {}
    total_errors = 0
    for profile_name, profile in FIXTURES.items():
        video_path = Path("storage/benchmark-fixtures") / profile["filename"]
        if args.regenerate_fixtures or not video_path.exists():
            generate_fixture(
                video_path,
                duration_seconds=profile["duration_seconds"],
                resolution=profile["resolution"],
                frame_rate=profile["frame_rate"],
                video_bitrate=profile["video_bitrate"],
            )

        raw_runs = []
        for run_number in range(1, args.runs + 1):
            print(
                f"[{profile_name}] run {run_number}/{args.runs}",
                flush=True,
            )
            result = run_benchmark(args.api_url, video_path, args.timeout)
            result["run_number"] = run_number
            raw_runs.append(result)
            total_errors += len(result["errors"])
            if not all(result["result"].values()) or result["errors"]:
                break

        completed_metrics = {
            metric: [
                run["metrics_seconds"][metric]
                for run in raw_runs
                if run["metrics_seconds"][metric] is not None
            ]
            for metric in METRICS
        }
        profiles[profile_name] = {
            "fixture": {
                "filename": profile["filename"],
                "size_bytes": video_path.stat().st_size,
                "duration_seconds": profile["duration_seconds"],
                "resolution": profile["resolution"],
                "frame_rate": profile["frame_rate"],
                "target_video_bitrate": profile["video_bitrate"],
            },
            "completed_runs": len(raw_runs),
            "summary_seconds": {
                metric: summarize(values)
                for metric, values in completed_metrics.items()
                if values
            },
            "raw_runs": raw_runs,
        }

    report = {
        "benchmark": "001-single-worker-repeated-size-comparison",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "runs_requested_per_profile": args.runs,
        "worker_count": 1,
        "execution": "sequential",
        "percentile_method": "nearest-rank",
        "total_errors": total_errors,
        "profiles": profiles,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

    complete = all(
        profile["completed_runs"] == args.runs for profile in profiles.values()
    )
    if not complete or total_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

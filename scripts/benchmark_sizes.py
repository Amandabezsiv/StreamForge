"""Benchmark the complete pipeline with small, medium, and large fixtures."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark_e2e import generate_fixture, run_benchmark

FIXTURES = {
    "small": {
        "filename": "baseline-small.mp4",
        "duration_seconds": 10,
        "resolution": "640x360",
        "frame_rate": 30,
        "video_bitrate": "1M",
    },
    "medium": {
        "filename": "baseline-medium.mp4",
        "duration_seconds": 30,
        "resolution": "1280x720",
        "frame_rate": 30,
        "video_bitrate": "3M",
    },
    "large": {
        "filename": "baseline-large.mp4",
        "duration_seconds": 60,
        "resolution": "1920x1080",
        "frame_rate": 30,
        "video_bitrate": "6M",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/001-single-worker-baseline/results-sizes.json"),
    )
    parser.add_argument("--regenerate-fixtures", action="store_true")
    args = parser.parse_args()

    runs = []
    for profile_name, profile in FIXTURES.items():
        video_path = Path("storage/benchmark-fixtures") / profile["filename"]
        if args.regenerate_fixtures or not video_path.exists():
            print(f"Generating {profile_name} fixture: {video_path}", flush=True)
            generate_fixture(
                video_path,
                duration_seconds=profile["duration_seconds"],
                resolution=profile["resolution"],
                frame_rate=profile["frame_rate"],
                video_bitrate=profile["video_bitrate"],
            )

        print(f"Benchmarking {profile_name} fixture", flush=True)
        result = run_benchmark(args.api_url, video_path, args.timeout)
        result["profile"] = {
            "name": profile_name,
            "duration_seconds": profile["duration_seconds"],
            "resolution": profile["resolution"],
            "frame_rate": profile["frame_rate"],
            "target_video_bitrate": profile["video_bitrate"],
        }
        runs.append(result)

        if not all(result["result"].values()) or result["errors"]:
            break

    report = {
        "benchmark": "001-single-worker-size-comparison",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "worker_count": 1,
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

    all_successful = all(
        all(run["result"].values()) and not run["errors"] for run in runs
    )
    if len(runs) != len(FIXTURES) or not all_successful:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

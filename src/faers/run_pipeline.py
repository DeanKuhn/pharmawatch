"""Run pipeline in one command rather than several individual ones."""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
log_path = Path("logs/cleansed_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)

STEPS = [
    ("download", True),
    ("parse", True),
    ("clean", True),
    ("merge", True),
    ("dedup", False),
    ("load", False),
]


def run_pipeline():
    parser = argparse.ArgumentParser(description="Run pipeline steps in one command.")
    parser.add_argument(
        "--start_from",
        default="download",
        choices=[s[0] for s in STEPS],
        help="Which step would you like to start the pipeline from?",
    )
    parser.add_argument(
        "--stop_after",
        default="load",
        choices=[s[0] for s in STEPS],
        help="Which step would you like to stop the pipeline after?",
    )
    args = parser.parse_args()

    for step_name, accepts_quarters in steps_to_run:
        start = time.time()
        log.info(f"--- Starting step {step_name} ---")
        cmd = ["uv", "run", "python", "-m", f"faers.{step_name}"]
        if accepts_quarters and args.quarters:
            cmd.extend(args.quarters)
        end = time.time()
        log.info(f"Step {step_name} completed in f{end - start}")

    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        log.error(f"Step '{step_name}' failed with code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    run_pipeline()

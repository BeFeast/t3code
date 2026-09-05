#!/usr/bin/env python3
"""Capture bounded job resource evidence without environment or process dumps."""
import datetime
import json
import os
from pathlib import Path
import platform
import subprocess

COUNTERS = ("memory.current", "memory.peak", "memory.events", "memory.stat", "memory.max", "memory.swap.max", "memory.pressure", "io.pressure", "cpu.stat", "cpu.max", "io.stat", "pids.current", "pids.max")


def read_cgroup(root):
    result = {}
    for name in COUNTERS:
        try:
            result[name] = {"value": (root / name).read_text().strip()}
        except OSError as error:
            result[name] = {"unavailable": type(error).__name__}
    return result


def command_value(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode:
            return {"unavailable": "exit " + str(result.returncode)}
        return {"value": result.stdout.strip()[:1024]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"unavailable": type(error).__name__}


def receipt():
    return {
        "schema_version": 1,
        "sampled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "job cgroup before post-action cleanup; counters are lifetime where supported",
        "phase_times": {"unavailable": "use Forgejo action_task_step timestamps"},
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "runner_name": os.environ.get("RUNNER_NAME", "")[:128] or None,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "source_sha": command_value(["git", "rev-parse", "HEAD"]),
        "toolchains": {name: command_value([name, "--version"])
                       for name in ("node", "pnpm", "rustc")},
        "cgroup": read_cgroup(Path("/sys/fs/cgroup")),
    }


if __name__ == "__main__":
    destination = Path("build/ci-resource-receipt.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt(), indent=2, sort_keys=True) + "\n")
    print("Saved job cgroup evidence to " + str(destination))

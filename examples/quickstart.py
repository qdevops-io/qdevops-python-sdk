"""Quickstart — the runnable version of the snippet in the README.

Submits a Bell-pair circuit to the simulator backend, waits for the
result, prints counts + fidelity. Drop this on any machine with the SDK
installed and `QDEVOPS_API_TOKEN` + `QDEVOPS_PROJECT_ID` exported, and
it should produce ~1.0 fidelity in under a second.

For the full standalone example with CI, lint config, and a Makefile,
see https://github.com/qdevops-io/bell-example.
"""

from __future__ import annotations

import os
import sys

from qdevops import APIError, Client


def main() -> int:
    token = os.environ.get("QDEVOPS_API_TOKEN", "").strip()
    project = os.environ.get("QDEVOPS_PROJECT_ID", "").strip()
    if not token or not project:
        print(
            "quickstart: set QDEVOPS_API_TOKEN and QDEVOPS_PROJECT_ID before running.",
            file=sys.stderr,
        )
        return 2

    client = Client(api_token=token)

    try:
        submission = client.submit_run(
            project_id=int(project),
            circuit="bell",
            backend="simulator",
            params={"shots": 4096},
        )
    except APIError as exc:
        print(f"quickstart: submit failed — HTTP {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1

    print(f"quickstart: runId={submission.run_id} queued, waiting…")
    run = client.wait_for_run(submission.run_id, timeout=300, poll_interval=2)

    if run.status != "succeeded" or run.result is None:
        reason = run.failure_reason or f"terminated as {run.status}"
        print(f"quickstart: run did not succeed — {reason}", file=sys.stderr)
        return 1

    counts = run.result.get("counts") or {}
    total = sum(int(v) for v in counts.values()) or 1
    good = int(counts.get("00", 0)) + int(counts.get("11", 0))
    fidelity = good / total

    print(f"quickstart: counts   = {counts}")
    print(f"quickstart: fidelity = {fidelity:.4f}")
    print(f"quickstart: duration = {run.duration_ms} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Architecture

This is a deeper walk-through of how a `client.submit_run(...)` call
becomes a real result on a quantum backend. If you only care about the
ten-second version, the diagram at the top of the [README](../README.md)
is enough.

## End-to-end flow

```mermaid
sequenceDiagram
    autonumber
    participant U as Your script
    participant S as qdevops SDK
    participant A as api.qdevops.io
    participant Q as SQS (per-env)
    participant W as Worker (Fargate)
    participant B as Backend<br/>(simulator / IBM / Braket)
    participant D as Dashboard<br/>(/bench, /runs/{id})

    U->>S: submit_run(project, circuit, backend, params)
    S->>A: POST /api/runs<br/>Authorization: Bearer pat_…
    A->>A: Authenticate token,<br/>check project membership,<br/>compute circuit_hash
    A->>Q: enqueue {run_id, circuit_hash, backend, params}
    A-->>S: 202 Accepted {run_id, status: queued}
    S-->>U: Submission(run_id=…)

    Q->>W: dequeue
    W->>W: resolve user backend creds<br/>(IBM/Braket only)
    W->>B: execute circuit
    B-->>W: counts + metadata
    W->>A: PATCH /api/runs/{id} status=succeeded, result, duration_ms

    loop wait_for_run() polls
        U->>S: wait_for_run(run_id)
        S->>A: GET /api/runs/{id}/status
        A-->>S: { status, isTerminal }
    end

    S->>A: GET /api/runs/{id}<br/>(once terminal)
    A-->>S: { result, duration_ms, ... }
    S-->>U: Run(result=…, duration_ms=…)

    A->>D: persist run; visible on /bench
```

## Components

### 1. SDK (this repo, eventually)

A pure-Python HTTP client. No quantum dependencies. Responsible for:

- Authentication (Bearer PAT or session cookie when called from a
  notebook on `qdevops.io`).
- Request shaping (the JSON body for `POST /api/runs`).
- Polling loop in `wait_for_run` (with exponential-ish backoff, capped
  at `poll_interval`).
- Mapping `APIError` from HTTP non-2xx responses, with `.status_code`,
  `.message`, and `.body` accessible.

The SDK explicitly does **not** simulate locally. The whole point of
having a server-side simulator backend is that a result you got today
can be reproduced byte-for-byte tomorrow regardless of your laptop.

### 2. Public API (`api.qdevops.io`)

Symfony / PHP. Responsible for:

- AuthN / AuthZ — token scopes, project membership, rate limits.
- Circuit hashing (so identical submissions can be deduplicated unless
  `force=True`).
- Enqueueing to the right per-environment SQS queue (`prod`,
  `staging`, `sandbox`).
- Persisting `BenchmarkRun` rows when `POST /api/benchmarks` is called
  with the `benchmarks:write` scope.

OpenAPI spec is the source of truth — see [`config/openapi/platform.yaml`](https://github.com/qdevops-io/qass)
in the platform repo.

### 3. Worker (Python on Fargate)

Long-lived ECS task consuming SQS. Responsible for:

- Fetching the user's backend credentials from the vault (IBM token,
  Braket key — never logged, never exposed back to the API).
- Translating the high-level `circuit` family + `params` into the
  vendor SDK's call (Qiskit / Braket / cuQuantum).
- Measuring `duration_ms` from start of execution to completion (does
  not include queue wait — that's computed by the API).
- Reporting the result back via `PATCH /api/runs/{id}`.

### 4. Backends

- **`simulator`** — server-side Qiskit Aer / NumPy / cuQuantum. No
  external credentials needed. Deterministic seedable.
- **`ibm`** — IBM Quantum. Routed through the user's `IBM_QUANTUM_TOKEN`.
  Queue wait is real and reported as `queue_wait_ms`.
- **`braket`** — AWS Braket. Routed through the user's AWS credentials.

### 5. Dashboard

The publishing side of the platform: `/bench` for the public benchmark
ledger, `/runs/{id}` for per-run forensics, project pages for team
visibility.

## What the SDK guarantees (and what it doesn't)

**Guarantees:**

- A `Run` whose `status == "succeeded"` has a non-`None` `result`.
- Every error path from `submit_run` is an `APIError` with structured
  fields, never a bare `requests.exceptions.HTTPError`.
- `wait_for_run` returns on terminal status or raises a timeout —
  never returns a still-running run.
- All datetime fields are ISO-8601 with explicit timezone.

**Does not guarantee:**

- That `result["counts"]` has any particular keys. Different circuit
  families produce different result shapes; consult the per-circuit
  docs.
- Bit-exact reproducibility across `env_id` changes. Pin `env_id` for
  reproducibility — see [reproducibility-example](https://github.com/qdevops-io/reproducibility-example).
- Idempotency on `submit_run`. Use `force=False` (the default) to lean
  on circuit-hash dedup, or supply your own idempotency key in
  `params["idempotency_key"]`.

## See also

- [bell-example](https://github.com/qdevops-io/bell-example) — the
  simplest possible end-to-end run.
- [Platform OpenAPI](https://github.com/qdevops-io/qass) — the
  authoritative API surface.
- [ROADMAP](../ROADMAP.md) — where this SDK is going.

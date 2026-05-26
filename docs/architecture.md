# Architecture

This is a deeper walk-through of how a `client.submit_run(...)` call
becomes a real result on a quantum backend. If you only care about the
ten-second version, the diagram at the top of the [README](../README.md)
is enough.

> Every Mermaid diagram below is also exported as PNG + SVG in the
> [diagram gallery](./diagrams/) for slides, blog posts, and Markdown
> viewers that can't render Mermaid inline.

## Contents

1. [End-to-end flow](#end-to-end-flow) — request to result, as a sequence
2. [Execution flow](#execution-flow) — every state a run can be in
3. [SQS orchestration](#sqs-orchestration) — three queues, per-stage routing
4. [Backend adapter flow](#backend-adapter-flow) — how a backend string becomes a vendor call
5. [ECR image pinning](#ecr-image-pinning) — why digests, not tags
6. [Reproducibility lifecycle](#reproducibility-lifecycle) — pinning a recipe in time
7. [Rerun lineage](#rerun-lineage) — `parent_run_id` and the run-tree
8. [Components](#components) — what each box owns
9. [What the SDK guarantees](#what-the-sdk-guarantees-and-what-it-doesnt) — and what it doesn't

## End-to-end flow

```mermaid
sequenceDiagram
    autonumber
    participant U as Your script
    participant S as qdevops SDK
    participant A as qdevops.io
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

    A->>D: persist run — visible on /bench
```

## Execution flow

Every run is an append-only timeline of typed events backed by the
`RunEvent` table (one row per state transition, queryable per-run on
`/runs/{id}`). This is what the actual state machine looks like —
straight from the `RunEvent::KIND_*` constants on the platform side:

```mermaid
stateDiagram-v2
    [*] --> submitted: POST /api/runs

    submitted --> deduped: circuit_hash hit\n(force=false)
    deduped --> [*]: returns cached result

    submitted --> queued: enqueue to SQS
    queued --> picked_up: worker dequeues
    queued --> skipped_cancelled: user cancelled\nbefore pickup

    picked_up --> started: adapter & creds ready
    picked_up --> errored: worker init failure

    started --> succeeded: counts produced
    started --> failed: vendor backend returned an error
    started --> errored: worker / SDK exception

    succeeded --> verdict_stamped
    failed --> verdict_stamped

    verdict_stamped --> artifact_uploaded
    artifact_uploaded --> [*]

    skipped_cancelled --> [*]
    errored --> [*]
```

A few non-obvious points worth calling out:

- **`deduped`** is a terminal state. With `force=false` (default), a
  resubmission whose circuit-hash matches a previous run short-circuits
  to the cached result without re-executing on the worker. Benchmark
  scripts always pass `force=true` because the queue-wait/duration
  metrics would otherwise be meaningless.
- **`verdict_stamped`** runs on both `succeeded` and `failed` because
  failure modes themselves are signal — they get a verdict too.
- **`errored`** vs **`failed`**: `failed` means the backend ran the
  circuit and reported an error condition (e.g. depolarising noise
  beyond threshold). `errored` means the worker/SDK itself crashed
  before producing a backend result. The dashboard treats them
  differently.

## SQS orchestration

The platform runs three production stages — `prod`, `staging`,
`sandbox` — each with its own SQS queue and its own worker consumer.
The dispatcher routes by `project.stage`, never by a query parameter,
so a sandbox project can never accidentally book a prod queue slot.

```mermaid
flowchart LR
    subgraph API["qdevops.io"]
        D["Dispatcher<br/>(reads project.stage)"]
    end

    D -->|stage=prod| Qp[("runs_prod")]
    D -->|stage=staging| Qs[("runs_staging")]
    D -->|stage=sandbox| Qsb[("runs_sandbox")]

    Qp -.->|N retries fail| DLQp[("runs_prod_dlq")]
    Qs -.->|N retries fail| DLQs[("runs_staging_dlq")]
    Qsb -.->|N retries fail| DLQsb[("runs_sandbox_dlq")]

    subgraph Fargate["Fargate worker task"]
        Cp["consumer: prod"]
        Cs["consumer: staging"]
        Csb["consumer: sandbox"]
    end

    Qp --> Cp
    Qs --> Cs
    Qsb --> Csb

    Cp & Cs & Csb --> Adapter["Backend adapter<br/>(see below)"]

    classDef q fill:#fef3c7,stroke:#d97706,color:#92400e;
    classDef dlq fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;
    class Qp,Qs,Qsb q;
    class DLQp,DLQs,DLQsb dlq;
```

- **One Fargate task, three consumers.** A single long-lived ECS task
  runs three Messenger consumer loops (`messenger:consume runs_prod
  runs_staging runs_sandbox`). One task is cheaper than three and a
  noisy stage can't starve the others — the loops have separate retry
  budgets.
- **DLQs are first-class.** An operator can replay a DLQ message
  (`KIND_REDISPATCHED` on the run timeline) without losing the
  original event trail.
- **No cross-stage routing.** A run can never move between stages —
  if you need to "promote" a sandbox recipe to prod, you submit a new
  run with the same `env_id` in the prod project.

## Backend adapter flow

The worker turns a backend string into a vendor SDK call through an
adapter layer. Adapters share one interface (`run(circuit, params) ->
counts + metadata`) but differ in credential handling and result
normalisation.

```mermaid
flowchart LR
    subgraph W["Worker process"]
        direction TB
        DQ["Dequeued message<br/>{run_id, circuit, backend, params, env_id}"]
        RES["Resolver:<br/>backend → adapter"]
        DQ --> RES
    end

    RES -->|backend=simulator| AS["SimulatorAdapter"]
    RES -->|backend=ibm| AI["IBMAdapter"]
    RES -->|backend=braket| AB["BraketAdapter"]

    AS --> QSim["qiskit-aer<br/>(local in-process)"]
    AI -->|GET project creds| V[("Credential vault<br/>(per-project)")]
    AB -->|GET project creds| V
    V -.->|IBM_QUANTUM_TOKEN| AI
    V -.->|AWS access key| AB
    AI --> IBM["IBMQ Runtime"]
    AB --> BRK["braket-sdk"]

    QSim & IBM & BRK -->|raw counts| Norm["Result normaliser"]
    Norm -->|"{counts, duration_ms,<br/>queue_wait_ms, vendor_meta}"| Out["PATCH /api/runs/{id}"]

    classDef vendor fill:#fef3c7,stroke:#d97706,color:#92400e;
    classDef ours fill:#f0fdf4,stroke:#16a34a,color:#14532d;
    class IBM,BRK vendor;
    class AS,AI,AB,QSim,Norm,V ours;
```

- **Credentials never leave the worker.** The SDK doesn't ship your IBM
  token to `qdevops.io` — the platform stores it encrypted in the
  vault and the worker pulls it just-in-time per run. Logs scrub the
  token before persistence (`EnvScrubDigestsCommand` handles cleanup).
- **One result schema, many backends.** The normaliser is what makes
  `run.result["counts"]` portable across vendors. Vendor-specific
  metadata lands under `run.result["vendor_meta"]` for users who need
  it.
- **Adding a new backend is one adapter class.** No SDK change, no API
  schema change, no client redeploy.

## ECR image pinning

The unit of reproducibility is the **ECR image digest**, not a tag.
Tags are mutable (`latest` drifts every push), digests are content-
addressed and immutable forever. Every `Environment` row stores a
digest, never a tag.

```mermaid
flowchart TB
    subgraph ECR["ECR: qass/python-runner"]
        direction TB
        Tag["tag: latest<br/>(mutable, points at HEAD)"]
        D1["digest sha256:abc…<br/>qiskit 1.0.2, aer 0.13.1<br/>numpy 1.26.4"]
        D2["digest sha256:def…<br/>qiskit 1.0.3, aer 0.13.1<br/>numpy 1.26.4"]
        D3["digest sha256:ghi…<br/>qiskit 1.1.0, aer 0.14.0<br/>numpy 2.0.0"]
        Tag -.->|today| D3
    end

    subgraph Env["Environment table"]
        direction TB
        E1["env_id=10<br/>label: pinned-Apr15<br/>image: sha256:abc…<br/>seed: 0x4f2c"]
        E2["env_id=24<br/>label: pinned-Jun02<br/>image: sha256:def…<br/>seed: 0x4f2c"]
        E3["env_id=42<br/>label: pinned-Sep11<br/>image: sha256:ghi…<br/>seed: 0xa193"]
    end

    E1 ===|immutable bind| D1
    E2 ===|immutable bind| D2
    E3 ===|immutable bind| D3

    Run1["Run #100<br/>env_id=10"] --> E1
    Run2["Run #287<br/>env_id=24"] --> E2
    Run3["Run #312<br/>env_id=42"] --> E3

    classDef img fill:#dbeafe,stroke:#1d4ed8;
    classDef env fill:#dcfce7,stroke:#16a34a;
    class D1,D2,D3 img;
    class E1,E2,E3 env;
```

- **`latest` is a moving target.** It's fine for the live container
  drift probe (`EnvironmentManager::detectDrift`), but no `Run` is ever
  resolved against a tag — only a digest.
- **Cleanup is digest-aware.** `EnvScrubDigestsCommand` checks the live
  `Environment` table before evicting a digest from ECR, so an old env
  that's still referenced never gets reaped.

## Reproducibility lifecycle

How "pinning" actually works from the user's perspective, end-to-end:

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant API as qdevops.io
    participant EM as EnvironmentManager
    participant CONT as Live Python container
    participant ECR as ECR

    Note over U,ECR: T0 — pin moment
    U->>API: POST /projects/{id}/environments/pin
    API->>EM: newEnvironment(project, stage, "pinned-Apr15", actor)
    EM->>CONT: probe live manifest<br/>(qiskit/aer/numpy versions, RNG seed)
    CONT-->>EM: {qiskit:1.0.2, aer:0.13.1, …, seed:0x4f2c}
    EM->>ECR: describe current HEAD image
    ECR-->>EM: digest sha256:abc…
    EM-->>API: Environment{id:10, image:sha256:abc…, manifest, seed}
    API-->>U: env_id=10, set as project default

    Note over U,ECR: T0 + 6 months (qiskit 1.1.0 is now `latest`)
    U->>API: POST /api/runs<br/>{circuit:"bell", env_id:10}
    API->>ECR: pull sha256:abc… (immutable)
    ECR-->>API: image (qiskit 1.0.2, exactly as in April)
    API->>CONT: run with pinned manifest + seed 0x4f2c
    CONT-->>API: counts (bit-identical to original run)

    Note over U,API: continuous drift check
    U->>API: GET /environments/10
    API->>EM: detectDrift(env)
    EM->>CONT: probe live manifest
    EM-->>API: drift report:<br/>{qiskit: 1.0.2 → 1.1.0,<br/>aer: 0.13.1 → 0.14.0}
    API-->>U: render `/environments/10`<br/>with drift badge
```

- **Pin = snapshot, not policy.** Pinning copies the live container's
  state into a row; the live container keeps moving. That's why the
  `Environment.show` page renders a drift report — it tells you how
  far the world has moved since you pinned.
- **Default-env auto-pinning.** `EnvironmentManager::ensureDefault`
  guarantees every project has at least one env, so a user who never
  visits `/environments` still gets reproducible runs on a sensible
  default.
- **RNG seed travels with the env.** Reproducing a result on
  `backend=simulator` requires the same seed; the platform stores it
  alongside the digest so "same env_id" really does mean "same bits".

## Rerun lineage

Every "reproduce" or "modify and resubmit" creates a *new* run with a
`parent_run_id` pointing at the source. `RunLineageService` walks the
chain and renders a per-dimension diff on `/runs/{id}/lineage`.

```mermaid
flowchart TD
    R0["Run #100<br/>circuit: bell<br/>mode: standard<br/>backend: simulator<br/>shots: 4096<br/>env_id: 10"]:::root

    R1["Run #142<br/>parent: #100<br/>mode: zne<br/>scaleFactors: 1,3"]
    R2["Run #143<br/>parent: #100<br/>mode: pec<br/>numSamples: 10"]

    R3["Run #287<br/>parent: #142<br/>scaleFactors: 1,3,5"]
    R4["Run #312<br/>parent: #287<br/>backend: ibm"]:::leaf

    R0 -->|reproduce + change mode<br/>RERUN_REQUESTED| R1
    R0 -->|reproduce + change mode<br/>RERUN_REQUESTED| R2
    R1 -->|refine extrapolation| R3
    R3 -->|switch backend| R4

    classDef root fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a;
    classDef leaf fill:#dcfce7,stroke:#16a34a,color:#14532d;
```

`RunLineageService::diff(parent, child)` emits one row per dimension
(circuit / mitigation / execution) with `status: same | changed`, so the
UI can render the diff like a `git diff` of run parameters. Concrete
example for `#100 → #142`:

| Dimension   | Field         | Status   | From       | To         |
| ----------- | ------------- | -------- | ---------- | ---------- |
| Circuit     | Circuit type  | same     | Bell (Φ⁺)  | Bell (Φ⁺)  |
| Mitigation  | Mode          | changed  | standard   | zne        |
| Mitigation  | Strategy      | changed  | none       | —          |
| Mitigation  | Extrapolation | changed  | —          | linear     |
| Mitigation  | Scale factors | changed  | —          | 1,3        |
| Execution   | Backend       | same     | simulator  | simulator  |
| Execution   | Shots         | same     | 4096       | 4096       |

A run's `RunEvent` timeline records `KIND_RERUN_REQUESTED` on the
parent side (audit) and `KIND_SUBMITTED` on the child side (intake), so
the lineage is reconstructable from events alone if the FK is ever
lost.

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

### 2. Public API (`qdevops.io`, canonical `api.qdevops.io`)

Symfony / PHP, served on the same hosts as the marketing site (the
nginx config terminates both `qdevops.io` and `api.qdevops.io` — once
the latter's CNAME is in DNS, both hostnames will serve the same
routes; the SDK's `QDEVOPS_BASE_URL` default of `https://qdevops.io`
will continue working unchanged).

Responsible for:

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

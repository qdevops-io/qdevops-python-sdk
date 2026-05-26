# Roadmap

The roadmap is a public commitment that this SDK isn't abandonware. Items
are ordered by priority; nothing here is a promised ship date. Open an
issue if you want to lobby for a re-ordering, or if your use case isn't
served by anything listed.

Legend: `[ ]` planned · `[~]` in progress · `[x]` shipped · `[!]` deferred

## Now — v0.2 (next ~quarter)

- [ ] **`AsyncClient`** — `asyncio`-native fan-out, so you can submit 100
  parameter sweeps in parallel without a thread pool. The blocking
  `Client` stays as the default for scripts and notebooks.
- [ ] **`qdevops` CLI** — install the package, get a `qdevops` binary on
  your `PATH`. First commands: `qdevops run`, `qdevops status <run_id>`,
  `qdevops bench`. Aim is feature parity with the SDK for shell / CI
  users who don't want to write Python.
- [ ] **Webhook callbacks** — register a URL and receive a `POST` when a
  run reaches a terminal state, instead of polling. Saves API calls in
  cron-driven workloads.
- [ ] **Typed result helpers** — `run.counts()`, `run.energy()`,
  `run.fidelity_to("|00>+|11>")` for the common quantities users hand-
  compute today. Strictly additive; raw `run.result` stays.

## Soon — v0.3

- [ ] **Pulse-level access** — submit OpenPulse / Braket pulse schedules
  alongside the high-level circuit DSL. Gated behind a `pulse:rw` scope.
- [ ] **Streaming intermediate VQE iterates** — server-sent events for
  iterative circuits so you can render convergence in real time.
- [ ] **Cancellation** — `client.cancel_run(run_id)` for runs that are
  still in the queue or executing on hardware that supports preemption.
- [ ] **Better error mitigation ergonomics** — `mode="zne"` today exposes
  raw extrapolation knobs; v0.3 ships sane defaults + a `ModeConfig`
  builder.
- [ ] **Result caching by `circuit_hash`** — opt-in client-side cache so
  identical submissions in a notebook don't round-trip.

## Later — v0.4+

- [ ] **First-class TypeScript SDK** — `@qdevops/sdk` on npm with the
  same surface. Same backend, same trust badges.
- [ ] **Notebook integrations** — `%qdevops` IPython magic for inline
  result rendering and one-click rerun.
- [ ] **OpenTelemetry export** — emit spans for `submit_run` →
  `wait_for_run` → result, so SREs can see quantum runs in the same
  dashboards as everything else.
- [ ] **Multi-tenant org client** — `Client.for_org("acme")` with
  scoped tokens and per-project rate budgets.

## Deferred / not now

- [!] **Local circuit simulation in the SDK.** The whole value prop is
  reproducibility on the server. Adding a client-side simulator
  bifurcates the result space ("does it work on my laptop?" stops
  meaning the same thing as "does it work on prod?"). Use the
  `simulator` backend.
- [!] **A bespoke circuit DSL.** The platform already accepts five
  named circuit families with parameters; that's enough composability
  for the use cases we've seen. If you want full OpenQASM submission,
  open an issue with the workload — we'll evaluate.

## How to influence this

- File an issue describing your use case and which roadmap item moves
  the needle. Concrete workloads are worth more than abstract requests.
- The [`qdevops-io`](https://github.com/qdevops-io) org's example repos
  are the easiest place to demonstrate a missing feature — open a PR
  there that *would work* if the feature existed, and we'll discuss.

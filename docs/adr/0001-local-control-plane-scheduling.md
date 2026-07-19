# ADR 0001: Use a local control-plane scheduling architecture

## Status

Accepted.

## Context

The Qt application previously mixed queue selection, resource limits, workspace preparation, process launch, runtime observation, and UI mutation in the same dispatch path. That made scheduling policy difficult to test and allowed direct submission to bypass queue scheduling.

The project needs the architectural benefits of a Slurm-like control plane, but it remains a single-machine desktop application. It does not need Slurm compatibility, daemon deployment, cluster protocols, or a copy of Slurm internals.

## Decision

Use these explicit Interfaces and Modules:

1. A Queue Item is adapted into an immutable Job Specification.
2. The Qt-free Scheduler Core consumes Job Specifications and a Resource Snapshot.
3. The Scheduler Core evaluates priority, submission order, dependencies, user holds, active conflicts, archive reservations, slots, and memory.
4. A successful scheduling decision creates an Allocation and a uniquely identified Execution Attempt.
5. JobRuntimeController acts as the local execution Adapter. It reports typed Execution Events instead of owning scheduler state.
6. The Scheduler State Repository persists job snapshots and append-only events in SQLite.
7. Queue Presentation projects scheduler snapshots back into existing Chinese UI statuses and messages.

Direct submission and formal-queue submission use the same Scheduler Core entry path. The old JSON queue remains a compatibility and presentation store during migration; scheduler lifecycle truth is persisted separately.

## State ownership

- Scheduler Core owns scheduling state and transition legality.
- JobRuntimeController owns QProcess and runtime evidence only.
- MemoryMonitorService and ProcessObservation provide resource and process observations.
- JobController coordinates Adapters but does not implement scheduling policy.
- MainWindow and QueueManagerDialog render projections and collect user intent.

Every Execution Event carries both `job_id` and `attempt_id`. Events for an older attempt are ignored.

## Recovery

Active scheduler entries without a current Queue Item are marked Lost at startup. Queue Items restored from an active UI state are first marked unknown and then reconciled through external-process observation. A corrupt SQLite database is preserved with a timestamped name before a new repository is created. Runtime state is stored in the Application Data Directory rather than the source tree.

## Consequences

Scheduling policy is independently testable without Qt. Adding another resource dimension or execution Adapter does not require rewriting the UI. Multiple local jobs may be allocated in one scheduling cycle instead of starting at most one per dispatch callback.

The application now maintains both JSON Queue Items and SQLite scheduler state, so the Queue Item Adapter must remain the only compatibility Seam until the JSON store can be retired or narrowed in a later migration.

# AbaqusSubmitter Context

AbaqusSubmitter is a desktop tool for submitting, monitoring, and managing Abaqus jobs.

## Product Direction

The Qt desktop application is the only product implementation and the only development mainline.

All application code lives in the canonical `abaqus_submitter` package. Do not introduce a second frontend package or any parallel product implementation.

## Implementation Boundaries

Keep the Scheduler Core and other UI-independent domain Modules free of Qt dependencies. Qt Adapters may depend on their Interfaces, but domain Modules must not import UI Modules.

Runtime state belongs under the operating system application-data directory, not in the source tree.

## Domain Language

- **Runtime Record**: one compatible mapping that contains the complete lifecycle state of an internally submitted or externally attached Abaqus run. Its construction and invariant-preserving transitions belong to the Runtime Record Module.
- **Job Orchestration Host**: the explicit set of application state and UI callbacks that job orchestration may use. Controllers must depend on this Interface instead of reflective MainWindow forwarding.
- **Queue Presentation**: the projection of Queue Items into candidate/formal table rows plus the coalescing of full and incremental refresh requests.
- **Process Observation**: one shared lifecycle for collecting a process snapshot and building PID and solver indexes consumed by runtime evidence, memory monitoring, and external-job discovery.
- **Restart Dependency Lifecycle**: resolution of an oldjob source, creation and persistence of its reference, workspace handoff, archive blocking, and reference invalidation.
- **Job Specification**: the stable, UI-independent description submitted to scheduling: identity, work directory, resource request, priority, dependencies, conflict key, and hold state.
- **Scheduler Core**: the Qt-free Deep Module that owns job ordering, dependency checks, resource allocation, pending reasons, and legal lifecycle transitions.
- **Resource Snapshot**: the scheduler input describing resources currently available for new local work. It is a point-in-time value, not mutable UI state.
- **Allocation**: the Scheduler Core decision that reserves resources for one Job Specification and creates a unique Execution Attempt.
- **Execution Attempt**: one fenced launch of a job, identified by `attempt_id`. Late events from an older attempt must not change the current attempt.
- **Execution Event**: the one-way report from local execution to the Scheduler Core, such as starting, started, completing, succeeded, failed, canceled, or lost.
- **Local Execution Backend**: the Interface implemented by the QProcess-based Adapter that launches and unregisters local Abaqus Execution Attempts. It reports outcomes through Execution Events.
- **Scheduler State Repository**: the SQLite-backed Adapter that transactionally persists scheduler snapshots and append-only event history for restart recovery.
- **Application Data Directory**: the operating-system-specific location that owns configuration, queue JSON, and Scheduler State Repository files outside the source tree.

## Operational Guardrails

Do not perform Git write operations without explicit confirmation.

Do not perform GitHub Issues write operations without explicit confirmation.

Chinese UI text, log text, and status strings must remain UTF-8.

If suspected mojibake is found, do not automatically fix it. Report the file path, line number, and text snippet in the final summary.

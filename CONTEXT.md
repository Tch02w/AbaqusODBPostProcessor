# AbaqusSubmitter Context

AbaqusSubmitter is a desktop tool for submitting, monitoring, and managing Abaqus jobs.

## Product Direction

The Qt version is the main development direction going forward.

The Tk version is retained as a legacy compatibility implementation and as a behavior reference for older functionality.

New features should be implemented in the Qt mainline first.

When migrating behavior from the legacy version, it is acceptable to read the Tk code as a behavioral reference, but do not directly reuse Tk modules from the Qt implementation.

Unless explicitly requested, do not modify the legacy Tk version.

## Implementation Boundaries

The Qt and Tk implementations must remain independent.

Do not cross-import between the Qt code and the Tk code.

## Domain Language

- **Runtime Record**: one compatible mapping that contains the complete lifecycle state of an internally submitted or externally attached Abaqus run. Its construction and invariant-preserving transitions belong to the Runtime Record Module.
- **Job Orchestration Host**: the explicit set of application state and UI callbacks that job orchestration may use. Controllers must depend on this Interface instead of reflective MainWindow forwarding.
- **Queue Presentation**: the projection of Queue Items into candidate/formal table rows plus the coalescing of full and incremental refresh requests.
- **Process Observation**: one shared lifecycle for collecting a process snapshot and building PID and solver indexes consumed by runtime evidence, memory monitoring, and external-job discovery.
- **Restart Dependency Lifecycle**: resolution of an oldjob source, creation and persistence of its reference, workspace handoff, archive blocking, and reference invalidation.

## Operational Guardrails

Do not perform Git write operations without explicit confirmation.

Do not perform GitHub Issues write operations without explicit confirmation.

Chinese UI text, log text, and status strings must remain UTF-8.

If suspected mojibake is found, do not automatically fix it. Report the file path, line number, and text snippet in the final summary.

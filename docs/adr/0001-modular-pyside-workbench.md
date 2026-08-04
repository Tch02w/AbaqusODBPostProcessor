# ADR 0001: Build one modular PySide6 Abaqus workbench

## Status

Accepted for the integration branch.

## Context

AbaqusSubmitter and AbaqusODBPostProcessor were independent desktop products.
Both already use Qt, but each owned its application startup, runtime paths,
process abstractions, visual policy, and top-level windows. Directly copying UI
code into either main window would preserve the existing coupling and make
future changes harder.

## Decision

The merged product uses four workspace packages:

1. `abaqus_workbench_core`: shared infrastructure without product workflow;
2. `abaqus_submitter`: submission, scheduling, monitoring, and remote execution;
3. `abaqus_odb_postprocessor`: ODB inspection, extraction, rendering, and results;
4. `abaqus_workbench`: the only integrated application composition root.

The process creates one PySide6 `QApplication`. PyQt fallback support is removed.
Only the composition root owns the integrated top-level window and event loop.
Component entry points remain compatibility launchers and use the same shared
application policy.

The desktop runtime targets CPython 3.15 and uses explicit lazy imports for
integrated pages and optional plotting. Abaqus `noGUI` programs remain written
for the Python version embedded by Abaqus and therefore do not use 3.15-only
syntax.

Submitter scheduling and postprocessor batch execution remain separate domain
modules. They exchange task events and paths through explicit contracts instead
of calling one another's window internals. A later resource broker may coordinate
their combined Abaqus process limit.

## Consequences

- Both original Git histories remain available in the monorepo.
- Shared paths, Qt binding, theme, command identity, process noise filtering,
  cancellation, and task events have a single implementation.
- Large existing window controllers can be split incrementally behind page
  adapters while all original tests continue to run.
- Abaqus `noGUI` scripts remain outside the desktop dependency graph.
- A Submitter job is handed to PostProcessor as a path through the composition
  root; neither component imports the other component's controller.

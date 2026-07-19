# ADR 0002: Consolidate on one Qt mainline

## Status

Accepted.

## Context

The repository contained a current Qt package, a Tk compatibility package, an older duplicate package, multiple root entry scripts, and several conflicting PyInstaller specifications. Maintaining those parallel Implementations reduced Locality and made it unclear which Interface future changes should target.

## Decision

The Qt application is the only product Implementation. Its canonical package name is `abaqus_submitter`.

- `abaqus_submitter` is the only application Module tree.
- `abaqussubmit.py`, `python -m abaqus_submitter`, and the project GUI script all enter through `abaqus_submitter.__main__.run`.
- Tk and older compatibility Implementations are removed rather than retained behind a Shallow forwarding Module.
- One PyInstaller specification produces `AbaqusSubmitter`.
- Configuration, queue state, and the Scheduler State Repository live in the Application Data Directory.

The internal Qt compatibility Module remains a real Seam because it has PySide6 and PyQt6 Adapters. This does not create a second application mainline.

## Consequences

Future development has one package, one entry Interface, one build definition, and one test suite. Deleting the duplicate Modules removes complexity instead of moving it to callers, while the canonical package increases Locality for application changes.

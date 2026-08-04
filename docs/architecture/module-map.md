# Module map

## Workbench shell

| Module | Responsibility |
|---|---|
| `abaqus_workbench.app` | Own the single `QApplication` and event loop |
| `abaqus_workbench.window` | Compose Submitter, PostProcessor, and result pages |
| `abaqus_workbench.routes` | Resolve explicit Submitter-to-PostProcessor path handoffs |

## Shared core

| Module | Responsibility |
|---|---|
| `abaqus_workbench_core.qt` | PySide6 imports and single-application policy |
| `abaqus_workbench_core.theme` | Process-wide Fusion palette and font |
| `abaqus_workbench_core.paths` | Platform application-data path policy |
| `abaqus_workbench_core.settings` | Atomic UTF-8 JSON settings |
| `abaqus_workbench_core.abaqus` | Abaqus command/release identity |
| `abaqus_workbench_core.processes` | Process contracts, cancellation, output filtering |
| `abaqus_workbench_core.events` | Cross-module task event contract |

## Submitter component

| Boundary | Existing modules |
|---|---|
| UI composition | `main`, `workbench_ui`, `cluster_ui`, `queue_manager`, `server_ui` |
| Scheduling domain | `scheduling`, `scheduler_adapter`, `scheduler_repository` |
| Execution adapters | `job_runtime`, `execution`, `remote_connection` |
| Runtime observation | `process_observation`, `process_scanner`, `memory_monitor` |
| ODB utilities | `odb_merge` |

## PostProcessor component

| Boundary | Existing modules |
|---|---|
| Application/page adapters | `application`, `postprocessor_page`, `result_browser_page` |
| UI composition | `app`, `batch_window`, `comparison_groups` |
| ODB discovery/compatibility | `discovery`, `runner`, `cache`, `abaqus_scripts/odb_compatibility.py` |
| Batch execution | `runner_parallel`, `process_runner` |
| Extraction domain | `postprocess`, `postprocess_core`, `legends`, `plotting` |
| Result storage | `result_assets`, `result_index` |
| Result presentation | `result_browser` |

The remaining oversized UI controllers are migration seams, not shared service
modules. Further extraction follows controller/service first, widget composition
second, and visual polish last. Domain algorithms are not moved merely to reduce
line counts.

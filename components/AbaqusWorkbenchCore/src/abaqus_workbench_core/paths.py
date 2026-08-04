"""Application-data path policy shared by all workbench components."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def resolve_application_data_dir(
    app_name: str,
    *,
    env_var: str = "",
    windows_scope: str = "local",
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Resolve an application data directory without creating it."""

    values = os.environ if environ is None else environ
    if env_var:
        override = str(values.get(env_var, "")).strip()
        if override:
            return Path(override).expanduser().resolve()

    active_platform = sys.platform if platform is None else platform
    if os.name == "nt" or active_platform.startswith("win"):
        variable = "APPDATA" if windows_scope == "roaming" else "LOCALAPPDATA"
        base = str(values.get(variable, "")).strip()
        if base:
            return Path(base) / app_name
        fallback = "Roaming" if windows_scope == "roaming" else "Local"
        return Path.home() / "AppData" / fallback / app_name

    if active_platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name

    base = str(values.get("XDG_DATA_HOME", "")).strip()
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / app_name


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Named runtime locations owned by one application component."""

    root: Path

    @classmethod
    def for_application(
        cls,
        app_name: str,
        *,
        env_var: str = "",
        windows_scope: str = "local",
    ) -> ApplicationPaths:
        return cls(
            resolve_application_data_dir(
                app_name,
                env_var=env_var,
                windows_scope=windows_scope,
            )
        )

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> ApplicationPaths:
        self.root.mkdir(parents=True, exist_ok=True)
        return self

import json
import os


def atomic_write_json(path, payload):
    """Atomically write JSON so joblist.json is never half-written."""
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


__all__ = ["atomic_write_json"]

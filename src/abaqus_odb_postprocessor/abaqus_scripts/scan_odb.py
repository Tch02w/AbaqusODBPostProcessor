"""Incremental Assembly-level scanner with an optional explicit ODB selection."""

from __future__ import print_function

import argparse
import json
import os
import sys


script_candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("scan_odb.py")
]
if not script_candidates:
    raise RuntimeError("Cannot locate scan_odb.py in argv: {0}".format(repr(sys.argv)))
script_path = os.path.abspath(script_candidates[-1])
base_path = os.path.join(os.path.dirname(script_path), "scan_odb_core.py")
namespace = {"__name__": "scan_odb_core_module"}
with open(base_path, "r", encoding="utf-8") as stream:
    base_source = stream.read()
exec(compile(base_source, base_path, "exec"), namespace, namespace)

arguments = sys.argv[1:]
if "--" in arguments:
    arguments = arguments[arguments.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("--folder", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--selection")
args = parser.parse_args(arguments)

folder = os.path.abspath(args.folder)
output = os.path.abspath(args.output)
all_paths = [
    os.path.join(folder, name)
    for name in sorted(os.listdir(folder))
    if name.lower().endswith(".odb") and os.path.isfile(os.path.join(folder, name))
]
if args.selection:
    with open(os.path.abspath(args.selection), "r", encoding="utf-8") as stream:
        requested = json.load(stream).get("paths", [])
    paths = []
    folder_key = os.path.normcase(os.path.normpath(folder))
    for value in requested:
        path = os.path.abspath(value)
        if (
            os.path.normcase(os.path.normpath(os.path.dirname(path))) == folder_key
            and path.lower().endswith(".odb")
            and os.path.isfile(path)
            and path not in paths
        ):
            paths.append(path)
else:
    paths = all_paths

report = {
    "folder": folder,
    "discovered_count": len(all_paths),
    "odb_count": len(paths),
    "completed_count": 0,
    "cancelled": False,
    "selected_paths": paths,
    "odbs": [],
}


def publish(message):
    print(message)
    sys.stdout.flush()


def save_partial():
    directory = os.path.dirname(output)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = output + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    if os.path.isfile(output):
        os.remove(output)
    os.rename(temporary, output)


publish("SCAN_DISCOVERED|{0}".format(len(paths)))
save_partial()
for index, path in enumerate(paths, 1):
    publish("SCAN_START|{0}|{1}|{2}".format(index, len(paths), os.path.basename(path)))
    report["odbs"].append(namespace["inspect_odb"](path))
    report["completed_count"] = index
    save_partial()
    publish("SCAN_DONE|{0}|{1}|{2}".format(index, len(paths), os.path.basename(path)))

publish("SCAN_FINISHED|{0}|{1}".format(len(paths), len(paths)))
print(json.dumps({"output": output, "odb_count": len(paths)}, ensure_ascii=False))


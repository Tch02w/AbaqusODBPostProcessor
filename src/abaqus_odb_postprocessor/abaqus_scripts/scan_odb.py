from __future__ import print_function

import argparse
import json
import os
import sys

from odbAccess import openOdb


def flatten(groups):
    for group in groups:
        try:
            for item in group:
                yield item
        except TypeError:
            yield group


def count_groups(groups):
    return len(list(flatten(groups)))


def bounding_box(nodes):
    coordinates = []
    for node in flatten(nodes):
        coordinates.append(tuple(float(value) for value in node.coordinates))
    if not coordinates:
        return None
    return {
        "x": [min(item[0] for item in coordinates), max(item[0] for item in coordinates)],
        "y": [min(item[1] for item in coordinates), max(item[1] for item in coordinates)],
        "z": [min(item[2] for item in coordinates), max(item[2] for item in coordinates)],
    }


def inspect_odb(path):
    result = {"path": path, "error": ""}
    odb = None
    try:
        odb = openOdb(path, readOnly=True)
        assembly = odb.rootAssembly
        step_names = list(odb.steps.keys())
        field_outputs = set()
        step_details = []
        for step_name in step_names:
            step = odb.steps[step_name]
            for frame in step.frames:
                field_outputs.update(frame.fieldOutputs.keys())
            step_details.append(
                {
                    "name": step_name,
                    "frame_count": len(step.frames),
                    "end_time": float(step.frames[-1].frameValue) if step.frames else 0.0,
                }
            )

        node_names = sorted(assembly.nodeSets.keys())
        element_names = sorted(assembly.elementSets.keys())
        details = {}
        for name in sorted(set(node_names + element_names)):
            node_set = assembly.nodeSets.get(name)
            element_set = assembly.elementSets.get(name)
            details[name] = {
                "node_count": count_groups(node_set.nodes) if node_set is not None else 0,
                "element_count": count_groups(element_set.elements)
                if element_set is not None
                else 0,
                "bbox": bounding_box(node_set.nodes) if node_set is not None else None,
            }

        instance_details = {}
        for name, instance in assembly.instances.items():
            element_types = {}
            for element in instance.elements:
                element_types[element.type] = element_types.get(element.type, 0) + 1
            instance_details[name] = {
                "nodes": len(instance.nodes),
                "elements": len(instance.elements),
                "element_types": element_types,
            }

        result.update(
            {
                "steps": step_names,
                "step_details": step_details,
                "field_outputs": sorted(field_outputs),
                "assembly_node_sets": node_names,
                "assembly_element_sets": element_names,
                "set_details": details,
                "instances": instance_details,
                "size_bytes": os.path.getsize(path),
            }
        )
    except Exception as exc:
        result["error"] = repr(exc)
    finally:
        if odb is not None:
            odb.close()
    return result


def parse_args():
    arguments = sys.argv[1:]
    if "--" in arguments:
        arguments = arguments[arguments.index("--") + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(arguments)


def main():
    args = parse_args()
    folder = os.path.abspath(args.folder)
    paths = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".odb"):
            paths.append(os.path.join(folder, name))
    report = {
        "folder": folder,
        "odb_count": len(paths),
        "odbs": [inspect_odb(path) for path in paths],
    }
    output = os.path.abspath(args.output)
    directory = os.path.dirname(output)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": output, "odb_count": len(paths)}))


if __name__ == "__main__":
    main()


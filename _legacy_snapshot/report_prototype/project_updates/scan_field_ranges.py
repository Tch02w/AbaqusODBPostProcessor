from __future__ import print_function

import argparse, json, math, os, sys
from odbAccess import openOdb


SPECS = (
    ("PILE_U_MAG", "U", "pile", "magnitude"),
    ("PILE_S_MISES", "S", "pile", "mises"),
    ("PILE_CON_DAMAGET", "DAMAGET", "concrete", "scalar"),
    ("PILE_CON_DAMAGEC", "DAMAGEC", "concrete", "scalar"),
    ("SOIL_PEEQ_XZ", "PEEQ", "soil", "scalar"),
    ("SOIL_PEMAG_XZ", "PEMAG", "soil", "scalar"),
    ("SOIL_S33_XZ", "S", "soil", "s33"),
    ("SOIL_S_MISES_XZ", "S", "soil", "mises"),
    ("REBAR_LONG_S_MISES_UNDEFORMED", "S", "rebar", "mises"),
    ("REBAR_LONG_S11_UNDEFORMED", "S", "rebar", "s11"),
)


def flatten(groups):
    for group in groups:
        try:
            for item in group:
                yield item
        except TypeError:
            yield group


def region_elements(assembly, region):
    names = list(getattr(region, "instanceNames", ()))
    if names and len(names) == len(region.elements):
        for index, group in enumerate(region.elements):
            for element in group:
                yield names[index], element
        return
    for element in flatten(region.elements):
        name = getattr(element, "instanceName", "")
        if not name:
            for candidate, instance in assembly.instances.items():
                try:
                    instance.getElementFromLabel(element.label)
                    name = candidate
                    break
                except Exception:
                    pass
        yield name, element


def longitudinal_data(assembly, region, material, threshold):
    node_maps, keys, geometry = {}, set(), {}
    for instance_name, element in region_elements(assembly, region):
        if not instance_name or not element.type.upper().startswith("T3D2"):
            continue
        category = str(getattr(getattr(element, "sectionCategory", None), "name", ""))
        if material.upper() not in category.upper():
            continue
        if instance_name not in node_maps:
            node_maps[instance_name] = dict(
                (node.label, tuple(float(x) for x in node.coordinates))
                for node in assembly.instances[instance_name].nodes
            )
        points = [node_maps[instance_name][label] for label in element.connectivity]
        first, last = points[0], points[-1]
        dx, dy, dz = last[0]-first[0], last[1]-first[1], last[2]-first[2]
        length = math.sqrt(dx*dx + dy*dy + dz*dz)
        if not length or abs(dz)/length < threshold:
            continue
        key = (instance_name, int(element.label))
        keys.add(key)
        geometry[key] = tuple(sum(point[axis] for point in points)/len(points) for axis in range(3))
    if not keys:
        raise RuntimeError("No {0} longitudinal T3D2 elements in {1}".format(material, region.name))
    return keys, geometry


def element_centroids(assembly, region):
    node_maps, output = {}, {}
    for instance_name, element in region_elements(assembly, region):
        if not instance_name:
            continue
        if instance_name not in node_maps:
            node_maps[instance_name] = dict(
                (node.label, tuple(float(x) for x in node.coordinates))
                for node in assembly.instances[instance_name].nodes
            )
        points = [node_maps[instance_name][label] for label in element.connectivity]
        output[(instance_name, int(element.label))] = tuple(
            sum(point[axis] for point in points)/len(points) for axis in range(3)
        )
    return output


def number(value, mode):
    if mode == "magnitude":
        try: return float(value.magnitude)
        except Exception: return math.sqrt(sum(float(x)**2 for x in value.data))
    if mode == "mises": return float(value.mises)
    if mode == "s33": return float(value.data[2])
    if mode == "s11":
        try: return float(value.data)
        except (TypeError, ValueError): return float(value.data[0])
    try: return float(value.data)
    except (TypeError, ValueError): return float(value.data[0])


def canonical_frames(odb, start_name, end_name):
    names = list(odb.steps.keys())
    start, end = names.index(start_name), names.index(end_name)
    sequence = 0
    for step_index in range(start, end+1):
        for frame_index, frame in enumerate(odb.steps[names[step_index]].frames):
            if step_index > start and frame_index == 0:
                continue
            yield sequence, step_index, names[step_index], frame_index, frame
            sequence += 1


def ring_coverage(high_damage_keys, centroids, z_min, z_max, z_bins=100, angle_bins=36):
    occupied = {}
    height = max(z_max-z_min, 1.0)
    for key in high_damage_keys:
        if key not in centroids: continue
        x, y, z = centroids[key]
        z_bin = min(z_bins-1, max(0, int((z-z_min)/height*z_bins)))
        angle = (math.atan2(y, x) + math.pi)/(2.0*math.pi)
        occupied.setdefault(z_bin, set()).add(min(angle_bins-1, int(angle*angle_bins)))
    return max([len(values)/float(angle_bins) for values in occupied.values()] or [0.0])


def scan(config):
    odb = openOdb(os.path.abspath(config["odb_path"]), readOnly=True)
    try:
        assembly = odb.rootAssembly
        regions = {
            "pile": assembly.elementSets[config["pile_display_set"]],
            "concrete": assembly.elementSets[config["pile_concrete_set"]],
            "soil": assembly.elementSets[config["soil_set"]],
            "rebar": assembly.elementSets[config["rebar_set"]],
        }
        material = str(config.get("longitudinal_material", "HRB400")).strip()
        threshold = float(config["settings"]["longitudinal_orientation_threshold"])
        rebar_keys, _ = longitudinal_data(assembly, regions["rebar"], material, threshold)
        concrete_centroids = element_centroids(assembly, regions["concrete"])
        z_values = [point[2] for point in concrete_centroids.values()]
        damage_limit = float(config["settings"]["damage_threshold"])
        coverage_limit = float(config["settings"].get("damage_angular_coverage", 0.9))
        catalog, fracture_sequence = [], None
        for seq, step_index, step_name, frame_index, frame in canonical_frames(
            odb, config["start_step"], config["end_step"]
        ):
            item = {"SequenceIndex": seq, "StepIndex": step_index, "StepName": step_name,
                    "FrameIndex": frame_index, "IncrementNumber": int(frame.incrementNumber),
                    "StepTime": float(frame.frameValue), "ranges": {}}
            high_damage, max_damage = set(), 0.0
            for spec, field_name, region_name, mode in SPECS:
                if field_name not in frame.fieldOutputs: continue
                try: values = frame.fieldOutputs[field_name].getSubset(region=regions[region_name]).values
                except Exception: continue
                selected = []
                for value in values:
                    key = (value.instance.name, int(getattr(value, "elementLabel", -1)))
                    if region_name == "rebar" and key not in rebar_keys: continue
                    try: scalar = number(value, mode)
                    except Exception: continue
                    if math.isfinite(scalar): selected.append(scalar)
                    if field_name == "DAMAGET":
                        max_damage = max(max_damage, scalar)
                        if scalar >= damage_limit: high_damage.add(key)
                if selected:
                    item["ranges"][spec] = {"min": min(selected), "max": max(selected), "value_count": len(selected)}
            coverage = ring_coverage(high_damage, concrete_centroids, min(z_values), max(z_values))
            item["MaxDAMAGET"] = max_damage
            item["DamageAngularCoverage"] = coverage
            item["FractureCandidate"] = max_damage >= damage_limit and coverage >= coverage_limit
            if item["FractureCandidate"] and fracture_sequence is None: fracture_sequence = seq
            catalog.append(item)
        prefracture = None if fracture_sequence in (None, 0) else fracture_sequence-1
        return {"odb_path": os.path.abspath(config["odb_path"]),
                "comparison_group": config.get("comparison_group", ""),
                "longitudinal_material": material, "longitudinal_element_count": len(rebar_keys),
                "frame_catalog": catalog,
                "auto_detection": {"method": "DAMAGET threshold plus angular coverage by depth bin",
                    "damage_threshold": damage_limit, "coverage_threshold": coverage_limit,
                    "first_fracture_sequence_index": fracture_sequence,
                    "prefracture_sequence_index": prefracture}}
    finally:
        odb.close()


def main():
    args = sys.argv[1:]
    if "--" in args: args = args[args.index("--")+1:]
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True); parser.add_argument("--output", required=True)
    parsed = parser.parse_args(args)
    with open(os.path.abspath(parsed.config), "r", encoding="utf-8") as stream: config = json.load(stream)
    payload = scan(config); output = os.path.abspath(parsed.output); directory = os.path.dirname(output)
    if directory and not os.path.isdir(directory): os.makedirs(directory)
    with open(output, "w", encoding="utf-8") as stream: json.dump(payload, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": output, "frames": len(payload["frame_catalog"])}))


if __name__ == "__main__": main()

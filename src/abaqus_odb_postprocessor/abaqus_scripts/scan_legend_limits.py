"""Fast contour-limit and damage-candidate scan using Abaqus' own legend engine."""

from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import argparse, json, math, os, sys
import visualization
import displayGroupOdbToolset as dgo


SPECS = (
    ("PILE_U_MAG", "U", "pile", (INVARIANT, "Magnitude"), NODAL, False),
    ("PILE_S_MISES", "S", "pile", (INVARIANT, "Mises"), INTEGRATION_POINT, False),
    ("PILE_CON_DAMAGET", "DAMAGET", "concrete", None, INTEGRATION_POINT, False),
    ("PILE_CON_DAMAGEC", "DAMAGEC", "concrete", None, INTEGRATION_POINT, False),
    ("SOIL_PEEQ_XZ", "PEEQ", "soil", None, INTEGRATION_POINT, True),
    ("SOIL_PEMAG_XZ", "PEMAG", "soil", None, INTEGRATION_POINT, True),
    ("SOIL_S33_XZ", "S", "soil", (COMPONENT, "S33"), INTEGRATION_POINT, True),
    ("SOIL_S_MISES_XZ", "S", "soil", (INVARIANT, "Mises"), INTEGRATION_POINT, True),
    ("REBAR_LONG_S_MISES_UNDEFORMED", "S", "rebar", (INVARIANT, "Mises"), INTEGRATION_POINT, False),
    ("REBAR_LONG_S11_UNDEFORMED", "S", "rebar", (COMPONENT, "S11"), INTEGRATION_POINT, False),
)


def flatten(groups):
    for group in groups:
        try:
            for item in group: yield item
        except TypeError: yield group


def region_elements(assembly, region):
    names = list(getattr(region, "instanceNames", ()))
    if names and len(names) == len(region.elements):
        for index, group in enumerate(region.elements):
            for element in group: yield names[index], element
        return
    for element in flatten(region.elements):
        name = getattr(element, "instanceName", "")
        if not name:
            for candidate, instance in assembly.instances.items():
                try: instance.getElementFromLabel(element.label); name = candidate; break
                except Exception: pass
        yield name, element


def element_data(assembly, region, material=None, direction_limit=None):
    node_maps, labels, centroids = {}, {}, {}
    for instance_name, element in region_elements(assembly, region):
        if not instance_name: continue
        if material is not None:
            category = str(getattr(getattr(element, "sectionCategory", None), "name", ""))
            if material.upper() not in category.upper() or not element.type.upper().startswith("T3D2"): continue
        if instance_name not in node_maps:
            node_maps[instance_name] = dict((node.label, tuple(float(x) for x in node.coordinates))
                                            for node in assembly.instances[instance_name].nodes)
        points = [node_maps[instance_name][label] for label in element.connectivity]
        if direction_limit is not None:
            first, last = points[0], points[-1]
            dx, dy, dz = last[0]-first[0], last[1]-first[1], last[2]-first[2]
            length = math.sqrt(dx*dx + dy*dy + dz*dz)
            if not length or abs(dz)/length < direction_limit: continue
        key = (instance_name, int(element.label))
        labels.setdefault(instance_name, []).append(int(element.label))
        centroids[key] = tuple(sum(point[axis] for point in points)/len(points) for axis in range(3))
    return labels, centroids


def set_group(viewport, spec_region, rebar_labels):
    viewport.odbDisplay.setValues(viewCut=OFF)
    if spec_region != "rebar":
        viewport.odbDisplay.displayGroup.replace(leaf=dgo.LeafFromElementSets(elementSets=(spec_region,)))
        return
    first = True
    for instance_name, values in sorted(rebar_labels.items()):
        leaf = dgo.LeafFromElementLabels(partInstanceName=instance_name,
                                         elementLabels=tuple(str(value) for value in sorted(values)))
        if first: viewport.odbDisplay.displayGroup.replace(leaf=leaf); first = False
        else: viewport.odbDisplay.displayGroup.add(leaf=leaf)


def primary(viewport, variable, refinement, position):
    if refinement is None:
        viewport.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=position)
    else:
        viewport.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=position, refinement=refinement)


def ring_coverage(frame, concrete_region, centroids, z_min, z_max, threshold, z_bins=100, angle_bins=36):
    if "DAMAGET" not in frame.fieldOutputs: return 0.0
    occupied, height = {}, max(z_max-z_min, 1.0)
    values = frame.fieldOutputs["DAMAGET"].getSubset(region=concrete_region).values
    for value in values:
        try: damage = float(value.data)
        except (TypeError, ValueError): damage = float(value.data[0])
        if damage < threshold: continue
        owner = getattr(value.instance, "name", ""); key = (owner, int(value.elementLabel))
        if key not in centroids: continue
        x, y, z = centroids[key]
        depth_bin = min(z_bins-1, max(0, int((z-z_min)/height*z_bins)))
        angle = (math.atan2(y, x)+math.pi)/(2.0*math.pi)
        occupied.setdefault(depth_bin, set()).add(min(angle_bins-1, int(angle*angle_bins)))
    return max([len(items)/float(angle_bins) for items in occupied.values()] or [0.0])


def scan(config):
    odb = session.openOdb(name=os.path.abspath(config["odb_path"]), readOnly=True)
    viewport = session.Viewport(name="Legend Limit Scanner", origin=(0, 0), width=160, height=110)
    viewport.setValues(displayedObject=odb); assembly = odb.rootAssembly
    concrete_name, soil_name = config["pile_concrete_set"], config["soil_set"]
    concrete_region = assembly.elementSets[concrete_name]
    _pile_labels, concrete_centroids = element_data(assembly, concrete_region)
    z_values = [point[2] for point in concrete_centroids.values()]; z_min, z_max = min(z_values), max(z_values)
    material = str(config.get("longitudinal_material", "HRB400")).strip()
    threshold = float(config["settings"]["longitudinal_orientation_threshold"])
    rebar_labels, rebar_centroids = element_data(assembly, assembly.elementSets[config["rebar_set"]], material, threshold)
    if not rebar_centroids: raise RuntimeError("No filtered longitudinal reinforcement found")

    names = list(odb.steps.keys()); start = names.index(config["start_step"]); end = names.index(config["end_step"])
    time_offsets, running = {}, 0.0
    for name in names:
        time_offsets[name] = running
        if odb.steps[name].frames: running += float(odb.steps[name].frames[-1].frameValue)
    catalog, sequence = [], 0
    for step_index in range(start, end+1):
        for frame_index, frame in enumerate(odb.steps[names[step_index]].frames):
            if step_index > start and frame_index == 0: continue
            catalog.append({"SequenceIndex": sequence, "StepIndex": step_index, "StepName": names[step_index],
                "FrameIndex": frame_index, "IncrementNumber": int(frame.incrementNumber),
                "StepTime": float(frame.frameValue), "TotalTime": time_offsets[names[step_index]]+float(frame.frameValue),
                "ranges": {}}); sequence += 1

    region_names = {"pile": config["pile_display_set"], "concrete": concrete_name,
                    "soil": soil_name, "rebar": "rebar"}
    for spec, variable, region_key, refinement, position, soil_section in SPECS:
        set_group(viewport, region_names[region_key], rebar_labels)
        if soil_section:
            cut_name = "FAST_XZ_" + spec
            cut = viewport.odbDisplay.ViewCut(name=cut_name, shape=PLANE,
                origin=(0.0, float(config["settings"]["soil_section_coordinate"]), 0.5*(z_min+z_max)),
                normal=(0.0, 1.0, 0.0), axis2=(0.0, 0.0, 1.0), followDeformation=OFF)
            cut.setValues(showModelAboveCut=OFF, showModelBelowCut=OFF, showModelOnCut=ON, showFreeBodyCut=OFF)
            viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(cut_name,))
        primary(viewport, variable, refinement, position)
        viewport.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
        viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)
        for item in catalog:
            frame = odb.steps[item["StepName"]].frames[item["FrameIndex"]]
            if variable not in frame.fieldOutputs: continue
            viewport.odbDisplay.setFrame(step=item["StepIndex"], frame=item["FrameIndex"])
            viewport.forceRefresh()
            item["ranges"][spec] = {"min": float(viewport.odbDisplay.contourOptions.autoMinValue),
                                     "max": float(viewport.odbDisplay.contourOptions.autoMaxValue)}

    damage_threshold = float(config["settings"]["damage_threshold"])
    coverage_threshold = float(config["settings"].get("damage_angular_coverage", 0.9))
    first_fracture = None
    for item in catalog:
        maximum = item["ranges"].get("PILE_CON_DAMAGET", {}).get("max", 0.0)
        coverage = 0.0
        if maximum >= damage_threshold:
            frame = odb.steps[item["StepName"]].frames[item["FrameIndex"]]
            coverage = ring_coverage(frame, concrete_region, concrete_centroids, z_min, z_max, damage_threshold)
        item["MaxDAMAGET"] = maximum; item["DamageAngularCoverage"] = coverage
        item["FractureCandidate"] = maximum >= damage_threshold and coverage >= coverage_threshold
        if item["FractureCandidate"] and first_fracture is None: first_fracture = item["SequenceIndex"]
    before = None if first_fracture in (None, 0) else first_fracture-1
    result = {"odb_path": os.path.abspath(config["odb_path"]), "comparison_group": config.get("comparison_group", ""),
        "longitudinal_material": material, "longitudinal_element_count": len(rebar_centroids),
        "range_method": "Abaqus contourOptions.autoMinValue/autoMaxValue on active display group",
        "frame_catalog": catalog, "auto_detection": {"method": "DAMAGET threshold plus angular coverage by depth bin",
        "damage_threshold": damage_threshold, "coverage_threshold": coverage_threshold,
        "first_fracture_sequence_index": first_fracture, "prefracture_sequence_index": before}}
    odb.close(); return result


def main():
    arguments = sys.argv[1:]
    if "--" in arguments: arguments = arguments[arguments.index("--")+1:]
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True); parser.add_argument("--output", required=True)
    args = parser.parse_args(arguments)
    with open(os.path.abspath(args.config), "r", encoding="utf-8") as stream: config = json.load(stream)
    result = scan(config); output = os.path.abspath(args.output); directory = os.path.dirname(output)
    if directory and not os.path.isdir(directory): os.makedirs(directory)
    with open(output, "w", encoding="utf-8") as stream: json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": output, "frames": len(result["frame_catalog"]), "method": result["range_method"]}))


if __name__ == "__main__": main()

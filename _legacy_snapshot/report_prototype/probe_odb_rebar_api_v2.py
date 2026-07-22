from __future__ import print_function

import inspect

from abaqus import session
from abaqusConstants import *
import visualization
import displayGroupOdbToolset as dgo


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"


def show(name, obj):
    print("=== {0} ===".format(name))
    print("repr:", repr(obj))
    print("doc:", repr(getattr(obj, "__doc__", None)))
    try:
        print("signature:", inspect.signature(obj))
    except Exception as exc:
        print("signature_error:", repr(exc))
    try:
        print("argspec:", inspect.getfullargspec(obj))
    except Exception as exc:
        print("argspec_error:", repr(exc))


odb = session.openOdb(name=ODB_PATH, readOnly=True)
assembly = odb.rootAssembly
print("materials:", list(odb.materials.keys()))
print("sections:", list(odb.sections.keys()))
for section_name, section in odb.sections.items():
    print("SECTION", section_name, "material=", getattr(section, "material", None), "type=", type(section))

region = assembly.elementSets["SET-REBAR"]
for group_index, group in enumerate(region.elements):
    items = list(group)
    print("GROUP", group_index, "count=", len(items))
    if not items:
        continue
    element = items[0]
    print(
        "SAMPLE",
        element.instanceName,
        element.label,
        element.type,
        "sectionCategory=",
        getattr(getattr(element, "sectionCategory", None), "name", None),
    )
    instance = assembly.instances[element.instanceName]
    print("INSTANCE", element.instanceName, "sectionAssignments=", repr(getattr(instance, "sectionAssignments", None)))

show("LeafFromElementLabels", dgo.LeafFromElementLabels)
show("LeafFromPartInstance", dgo.LeafFromPartInstance)
show("LeafFromElementSets", dgo.LeafFromElementSets)
for leaf_name in sorted(name for name in dir(dgo) if name.startswith("LeafFrom")):
    print("LEAF", leaf_name)
show("session.Spectrum", session.Spectrum)
show("graphicsOptions.setValues", session.graphicsOptions.setValues)
show("printOptions.setValues", session.printOptions.setValues)
odb.close()

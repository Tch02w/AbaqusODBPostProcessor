from abaqus import session
from abaqusConstants import *
import visualization


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"


def matching_names(obj, tokens):
    names = []
    for name in dir(obj):
        lower_name = name.lower()
        if any(token.lower() in lower_name for token in tokens):
            names.append(name)
    return sorted(names)


print("SESSION_FREE_BODY_METHODS", matching_names(session, ("freebody", "free_body")))

viewport = session.Viewport(name="ODB API Probe", origin=(0, 0), width=120, height=80)
odb = session.openOdb(name=ODB_PATH, readOnly=True)
viewport.setValues(displayedObject=odb)

print(
    "ODB_DISPLAY_CUT_METHODS",
    matching_names(viewport.odbDisplay, ("cut", "freebody", "free_body")),
)
print(
    "VIEW_CUT_OPTION_MEMBERS",
    matching_names(viewport.odbDisplay.viewCutOptions, ("cut", "freebody", "free_body")),
)

cut = viewport.odbDisplay.ViewCut(
    name="XY_PROBE",
    shape=PLANE,
    origin=(0.0, 0.0, 15750.0),
    normal=(0.0, 0.0, 1.0),
    axis2=(1.0, 0.0, 0.0),
    followDeformation=OFF,
)
print("VIEW_CUT_MEMBERS", matching_names(cut, ("cut", "freebody", "free_body", "area")))

try:
    cut.setValues(
        showModelAboveCut=OFF,
        showModelBelowCut=ON,
        showModelOnCut=ON,
        showFreeBodyCut=ON,
    )
    print("FREE_BODY_ENABLE", "SUCCESS")
    print("FREE_BODY_AREA", cut.crossSectionalArea)
except Exception as exc:
    print("FREE_BODY_ENABLE", "FAILED", repr(exc))

odb.close()

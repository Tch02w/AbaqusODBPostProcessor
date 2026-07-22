from abaqus import session
from abaqusConstants import *
import visualization


def names_matching(obj, tokens):
    return sorted(
        name
        for name in dir(obj)
        if any(token.lower() in name.lower() for token in tokens)
    )


print("SESSION", names_matching(session, ("displaygroup", "leaf")))
print("VISUALIZATION", names_matching(visualization, ("displaygroup", "leaf")))

for module_name in (
    "displayGroupOdbToolset",
    "displayGroupMdbToolset",
    "displayGroup",
    "odbDisplay",
):
    try:
        module = __import__(module_name)
        print(module_name, "IMPORTED", names_matching(module, ("displaygroup", "leaf")))
    except Exception as exc:
        print(module_name, "FAILED", repr(exc))

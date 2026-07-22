from __future__ import print_function

import inspect

from abaqus import session
import displayGroupOdbToolset as dgo


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


show("LeafFromElementLabels", dgo.LeafFromElementLabels)
show("LeafFromPartInstance", dgo.LeafFromPartInstance)
show("session.Spectrum", session.Spectrum)
show("graphicsOptions.setValues", session.graphicsOptions.setValues)
show("printOptions.setValues", session.printOptions.setValues)

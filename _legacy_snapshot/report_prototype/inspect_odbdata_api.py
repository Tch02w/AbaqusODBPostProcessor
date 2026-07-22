from abaqus import session
import os
import visualization


odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
data = session.odbData[odb.name]
path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "odbdata_api.txt"))
with open(path, "w", encoding="utf-8") as stream:
    stream.write("TYPE\n" + repr(type(data)) + "\n")
    stream.write("DIR\n" + repr(dir(data)) + "\n")
    for name in dir(data):
        if "active" in name.lower() or "frame" in name.lower() or "step" in name.lower():
            try:
                value = getattr(data, name)
                stream.write(name + "=" + repr(value) + "\n")
            except Exception as exc:
                stream.write(name + " ERROR " + repr(exc) + "\n")
odb.close()

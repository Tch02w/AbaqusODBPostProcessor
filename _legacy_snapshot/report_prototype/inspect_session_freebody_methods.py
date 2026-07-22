from abaqus import session
import os
import visualization


names = [name for name in dir(session) if "free" in name.lower() or "body" in name.lower()]
path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "session_freebody_methods.txt"))
with open(path, "w", encoding="utf-8") as stream:
    for name in names:
        stream.write(name + "\n")

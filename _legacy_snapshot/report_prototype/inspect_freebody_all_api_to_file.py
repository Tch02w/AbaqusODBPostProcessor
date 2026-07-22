from abaqus import session
import os
import visualization


path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "freebody_api.txt"))
with open(path, "w", encoding="utf-8") as stream:
    stream.write("WRITE_FREE_BODY_REPORT_DOC\n")
    stream.write(repr(session.writeFreeBodyReport.__doc__) + "\n")
    stream.write("WRITE_FREE_BODY_REPORT_DIR\n")
    stream.write(repr(dir(session.writeFreeBodyReport)) + "\n")
    stream.write("SESSION_ODB_DATA_DIR\n")
    stream.write(repr(dir(session.odbData)) + "\n")

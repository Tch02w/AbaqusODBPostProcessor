from abaqus import session
import visualization


print("WRITE_FREE_BODY_REPORT_DOC")
print(session.writeFreeBodyReport.__doc__)
print("SESSION_ODB_DATA_TYPE")
print(type(session.odbData))
print("SESSION_ODB_DATA_DIR")
print([name for name in dir(session.odbData) if "active" in name.lower() or "frame" in name.lower()])

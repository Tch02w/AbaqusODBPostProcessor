from __future__ import print_function

import json
import os
import sys


path = os.path.join(os.getcwd(), "abaqus_argv.json")
with open(path, "w", encoding="utf-8") as stream:
    json.dump({"cwd": os.getcwd(), "argv": sys.argv}, stream, ensure_ascii=False, indent=2)


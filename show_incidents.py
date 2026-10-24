import sys
sys.path.insert(0, "app")
from db import get_db

for r in get_db().execute("SELECT * FROM incidents ORDER BY id"):
    print(dict(r))

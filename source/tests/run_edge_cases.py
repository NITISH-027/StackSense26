"""Run every edge-case scenario and print a summary table.

Usage: python3 tests/run_edge_cases.py
Exit code 0 always; the point is inspection, not assertions.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from stacksense.engine import recommend  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "edge_scenarios")

for name in sorted(os.listdir(HERE)):
    if not name.endswith(".json"):
        continue
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        scenario = json.load(f)
    r = recommend(scenario)
    stack = r.get("stack") or {}
    core = " / ".join((stack.get(l) or {}).get("choice", "-") for l in
                      ["frontend", "backend", "database", "hosting"]) if stack else "(no stack)"
    print(f"{name}")
    print(f"  status: {r['status']}")
    if r["validation_errors"]:
        for e in r["validation_errors"]:
            print(f"  validation: {e['field']}: {e['message']}")
    for w in r["warnings"]:
        print(f"  [{w['severity']}] {w['code']}: {w['message'][:110]}")
    if stack:
        print(f"  stack: {core}")
    print()

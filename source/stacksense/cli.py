"""Command-line interface for the StackSense engine.

Usage:
  python3 -m stacksense recommend SCENARIO.json [-o OUTPUT.json]
  python3 -m stacksense batch SCENARIOS_DIR [-o OUTPUTS_DIR]
  python3 -m stacksense validate SCENARIO.json
  python3 -m stacksense ui [--port 8000]

The engine makes no LLM calls at all; --llm accepts only "off" and exists
so the mandated LLM-off interface is explicit. Every run is identical for
identical input (deterministic by construction).
"""

import argparse
import json
import os
import sys

from .engine import recommend
from . import ENGINE_VERSION


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(result, out_path):
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stacksense",
                                     description="StackSense: deterministic tech stack recommendation engine")
    parser.add_argument("--llm", default="off", choices=["off"],
                        help="LLM features switch. This engine makes no LLM calls; only 'off' exists.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("recommend", help="Recommend a stack for one scenario JSON file")
    p.add_argument("scenario")
    p.add_argument("-o", "--output", default=None)

    p = sub.add_parser("batch", help="Run every *.json scenario in a directory")
    p.add_argument("scenarios_dir")
    p.add_argument("-o", "--outputs-dir", default=None)

    p = sub.add_parser("validate", help="Validate a scenario file against the input schema")
    p.add_argument("scenario")

    p = sub.add_parser("ui", help="Serve the web UI (stdlib http server, no dependencies)")
    p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.cmd == "recommend":
        result = recommend(_load(args.scenario))
        _write(result, args.output)
        return

    if args.cmd == "batch":
        outdir = args.outputs_dir
        for name in sorted(os.listdir(args.scenarios_dir)):
            if not name.endswith(".json"):
                continue
            result = recommend(_load(os.path.join(args.scenarios_dir, name)))
            sid = result.get("scenario_id", name[:-5])
            out = os.path.join(outdir, f"{sid}_output.json") if outdir else None
            _write(result, out)
            print(f"{name}: status={result['status']} -> {out or 'stdout'}", file=sys.stderr)
        return

    if args.cmd == "validate":
        result = recommend(_load(args.scenario))
        if result["validation_errors"]:
            for e in result["validation_errors"]:
                print(f"ERROR {e['field']}: {e['message']}")
            sys.exit(1)
        print("Valid.")
        return

    if args.cmd == "ui":
        from .ui_server import serve
        serve(args.port)


if __name__ == "__main__":
    main()

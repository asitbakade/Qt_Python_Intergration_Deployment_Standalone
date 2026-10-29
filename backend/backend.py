"""
backend.py - the Python back end of QtPythonDemo
=================================================
The Qt front end starts this script as a child process and talks to it over
stdin / stdout, one JSON object per line:

    request   {"id": 1, "method": "add", "params": {"a": 2, "b": 3}}
    response  {"id": 1, "result": 5}
    error     {"id": 1, "error": "TypeError: ..."}

On start-up the back end announces itself with {"event": "ready", ...}.
It exits when stdin is closed (the front end does that when it quits).

Add your own functions to METHODS - that is the whole integration point.
Only the "result" must be JSON-serialisable (numbers, strings, lists, dicts).

Run it by hand to try the protocol:
    python backend.py
    {"id": 1, "method": "add", "params": {"a": 2, "b": 3}}
"""
import json
import platform
import sys
import time

import numpy as np          # a third-party package: shipped inside the bundle by tools/package.py


# ---------------------------------------------------------------- the API

def add(a, b):
    return a + b


def stats(values):
    arr = np.asarray(values, dtype=float)
    return {
        "count": int(arr.size),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def info():
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "executable": sys.executable,
        "cwd": __import__("os").getcwd(),
    }


def slow(seconds=3):
    """Shows that the UI stays responsive: the front end never waits for us."""
    time.sleep(float(seconds))
    return {"slept": seconds}


METHODS = {
    "add": add,
    "stats": stats,
    "info": info,
    "slow": slow,
}


# ---------------------------------------------------------------- the loop

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()                      # the front end reads line by line: never buffer


def main():
    # utf-8 on both pipes whatever the Windows code page is
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stdin.reconfigure(encoding="utf-8")

    send({"event": "ready", "python": platform.python_version(), "numpy": np.__version__})

    for line in sys.stdin:                  # ends when the front end closes our stdin
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = METHODS[req["method"]]
            params = req.get("params") or {}
            send({"id": req_id, "result": method(**params)})
        except Exception as exc:            # one bad request must not kill the back end
            send({"id": req_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"error handling {line!r}: {exc!r}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

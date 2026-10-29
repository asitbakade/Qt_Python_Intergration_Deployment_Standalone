# Qt + Python in one standalone Windows bundle

A minimal, complete example of a **Qt (C++ / QML) front end** that drives a
**Python back end**, packaged into **one folder that runs on any Windows PC
without installing Qt, Python or any package**.

Use it as the template for a real application: replace `backend/backend.py`
with your own Python, keep the three files of the front end, run one command,
ship the folder.

![QtPythonDemo](docs/screenshot.png)

```
   QtPythonDemo.exe (Qt 6, C++ + QML)                backend/python/python.exe backend/backend.py
   ┌──────────────────────────────┐   stdin (JSON)   ┌───────────────────────────────────────┐
   │ Main.qml  ──►  Backend (C++) │ ───────────────► │ {"id":1,"method":"add","params":{..}} │
   │           ◄──  QProcess      │ ◄─────────────── │ {"id":1,"result":5}                    │
   └──────────────────────────────┘   stdout (JSON)  └───────────────────────────────────────┘
```

The front end starts the Python as a child process and exchanges one JSON
object per line. No sockets, no ports, no extra libraries on either side.

## Contents

| Path | What it is |
|------|------------|
| `frontend/main.cpp` | starts the QML engine (and the `--selftest` / `--screenshot` helpers) |
| `frontend/backend.h`, `backend.cpp` | **the integration**: `Backend` runs the Python with `QProcess`, sends requests, receives answers, exposed to QML as `Backend { }` |
| `frontend/qml/Main.qml` | the demo UI: three buttons that call Python, a live log of the JSON traffic |
| `backend/backend.py` | **the Python side**: a dictionary of functions and a 20-line loop that reads requests and writes answers |
| `backend/requirements.txt` | the Python packages the back end needs (here `numpy`, to show that third-party packages ship too) |
| `tools/package.py` | **one command** that builds the exe, adds the Qt runtime, adds a private Python with the packages, tests the result and zips it |
| `tools/check_deps.py` | release check used by `package.py`: every DLL the bundle needs is inside the bundle |
| `CMakeLists.txt` | the Qt project (open it in Qt Creator) |

## Step 1 - install the tools (build PC only)

1. **Qt 6** with the *MinGW 64-bit* kit, plus *CMake* and *Ninja* from the same Qt installer
   (they land in `C:\Qt\Tools`). Tested with Qt 6.9.2.
2. **Python 3** (3.10 or newer) with pip. Tested with 3.13. Any install is fine, also the
   one from the Microsoft Store.
3. **Git** (only to clone).

```bat
git clone https://github.com/asitbakade/Qt_Python_Intergration_Deployment_Standalone.git
cd Qt_Python_Intergration_Deployment_Standalone
pip install -r backend\requirements.txt
```

## Step 2 - run it in development

Open `CMakeLists.txt` in Qt Creator, choose the *Desktop Qt 6.x MinGW 64-bit* kit, press **Run**.

The app finds `backend/backend.py` in the source tree and runs it with the `python` on your
PATH (the path is baked in by CMake, see `Backend::locate()` in `backend.cpp`). The status
dot turns green when the Python has printed its `ready` line. Press **Add**, **Compute** and
**Back end info**: every request and answer is shown in the log at the bottom.

You can also talk to the back end by hand:

```bat
python backend\backend.py
{"id": 1, "method": "add", "params": {"a": 2, "b": 3}}
```

## Step 3 - build the standalone bundle

```bat
python tools\package.py
```

(add `--qt C:\Qt\6.9.2\mingw_64` if you have several Qt versions). The script prints each
step. About 2 minutes the first time (it downloads the 11 MB embeddable Python once and
caches it in `build\cache`). Result:

```
dist\QtPythonDemo\                      170 MB, runs anywhere
dist\QtPythonDemo-windows-x86_64.zip     64 MB, what you send
```

Inside `dist\QtPythonDemo`:

```
QtPythonDemo.exe              our front end
Qt6Core.dll, Qt6Quick.dll ... the Qt runtime            <- windeployqt
qml\                          the QML modules we import  <- windeployqt (--qmldir)
libstdc++-6.dll, libgcc_s_seh-1.dll ...  MinGW runtime   <- windeployqt
backend\backend.py            our Python code
backend\python\python.exe     a complete private Python  <- python.org "embeddable" zip
backend\python\Lib\site-packages\numpy\  the packages     <- pip install --target
README.txt
```

## Step 4 - test it like the customer will

Copy the folder (or unzip the zip) to another PC, or to a folder on this PC, and
double-click `QtPythonDemo.exe`. Nothing is installed, nothing on the PC is used.

Without clicking, from a command prompt inside the folder:

```bat
QtPythonDemo.exe --selftest
echo %errorlevel%
```

`0` means the front end started the bundled Python, called `add(2, 3)` and got `5` back.
(`1` = the back end failed, `2` = no answer within 15 s.) Use this on the target PC, or in
CI, before shipping.

That is all: four steps, one command. The rest of this file explains how the pieces
work and how to put your own project in.

## How the integration works

**Python side** (`backend/backend.py`): a dictionary `METHODS = {"add": add, "stats": stats, ...}`
and a loop:

```python
send({"event": "ready", ...})          # tells the front end we are up
for line in sys.stdin:                  # one request per line
    req = json.loads(line)
    result = METHODS[req["method"]](**req["params"])
    send({"id": req["id"], "result": result})
```

`send` writes one line to stdout and **flushes** it. Errors go back as
`{"id": .., "error": "..."}`, so one bad request never kills the process. The loop ends
when stdin is closed, which the front end does on exit.

**C++ side** (`frontend/backend.cpp`): `Backend::start()` finds the Python (bundle first,
then PATH), sets `PYTHONUNBUFFERED=1` and `PYTHONIOENCODING=utf-8`, and starts it with
`QProcess`. `call(method, params)` writes one JSON line and returns a request id;
`onStdout()` splits the output into lines and emits `replied(id, method, result)` or
`failed(id, method, error)`. Nothing blocks the UI: the 3-second "slow call" in the demo
leaves the window fully responsive.

**QML side** (`frontend/qml/Main.qml`):

```qml
Backend { id: backend; Component.onCompleted: start()
          onReplied: (id, method, result) => { if (method === "add") label.text = result } }
Button  { onClicked: backend.call("add", { a: 2, b: 3 }) }
```

**Why a private Python instead of PyInstaller?** The bundle carries the official CPython
(the "embeddable" zip from python.org, 11 MB) and your packages exactly as pip installed
them. Nothing is repacked, so large or awkward packages (numpy, scipy, torch, models) work
the same as in development, and a problem is debugged by running
`backend\python\python.exe backend\backend.py` in the bundle. PyInstaller is a fine
alternative for a small pure-Python back end: then `backend.py` becomes `backend.exe` and
`Backend::locate()` starts that instead.

**Why stdin/stdout JSON?** It needs nothing: no ZeroMQ, no gRPC, no port that a firewall or
another instance can block, and the traffic is readable in the log pane. When you need
streaming or several clients, the same `Backend` class can be moved to a local socket; the
packaging does not change.

## Putting your own project in

1. Copy your Python package next to `backend/backend.py` (or into a sub folder) and import
   it there; add one entry to `METHODS` per function the UI may call. Results must be JSON
   (numbers, strings, lists, dicts; convert numpy with `.tolist()` / `float()`).
2. Put the packages in `backend/requirements.txt` (pin versions for reproducible bundles).
3. Replace `Main.qml` with your UI; keep `Backend { }` and call `backend.call(...)`.
4. Rename `QtPythonDemo` in `CMakeLists.txt` and `tools/package.py` (`APP = ...`).
5. Data files (models, weights, configs) go under `backend/`; `package.py` copies the whole
   folder. Big files that must not be in git are downloaded by a step you add to
   `package.py` or checked by a script like PS12's `check_assets.py`.
6. Run `python tools\package.py`. The release check at the end fails if any `.pyd` or DLL in
   the bundle would need a library from your PC (e.g. a Visual C++ runtime a package
   expects): then copy that DLL into `backend\python\` from the package's wheel or the
   VC++ Redistributable, inside `package.py`, next to the existing `msvcp140.dll` step.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Status *error: no Python back end found* | In development: no `python` on PATH. In the bundle: `backend\python\python.exe` missing, copy the whole folder |
| Status *error: back end exited with code 1* | Python raised at start-up (import error ...). Run `backend\python\python.exe -u backend\backend.py` in a prompt to see the traceback |
| Request answered, but late | Output buffering: keep `flush()` in `send()` and `-u` / `PYTHONUNBUFFERED=1` (both already there) |
| `ModuleNotFoundError` in the bundle only | The package is not in `requirements.txt`, or `python313._pth` lost its `Lib\site-packages` line |
| *The specified module could not be found* on the target PC | A DLL from your PC was needed: `python tools\check_deps.py dist\QtPythonDemo` names it; ship it inside `backend\python\` |
| Antivirus / SmartScreen warning on the target PC | Unsigned executable. Sign `QtPythonDemo.exe` with your code-signing certificate (signtool) after `package.py` |
| A console window flashes | Only if the exe was built without `WIN32_EXECUTABLE`; the Python child never shows a console because Qt starts it with `CREATE_NO_WINDOW` |
| Bundle too large | `opengl32sw.dll` (20 MB, software OpenGL fallback) and `D3Dcompiler_47.dll` can be deleted when every target PC has a working GPU driver; `--no-quick-controls2` style folders under `qml\` you do not use can go too |

## Linux

The same code builds on Linux. For the bundle use `linuxdeployqt` or Qt's
`qt_generate_deploy_qml_app_script` for the Qt part and `python -m venv --copies` (or
`python-build-standalone`) for a relocatable Python; `Backend::locate()` looks for
`backend/python/bin/python3` first on that platform. Not covered by this repository.

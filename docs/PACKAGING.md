# Inside `tools/package.py`

**How a Qt front end and a Python back end become one standalone bundle, step by step, on Windows and Linux.**

This document is the long version of the README's *Step 3*. It explains what the packaging script
does, which tool does each part, why that tool, and what ends up in the bundle. Read it when you
want to understand the mechanism, adapt it to your own application, or debug a bundle that does not
start on a customer's machine.

---

## Contents

1. [The goal](#1-the-goal)
2. [Two halves, two problems](#2-two-halves-two-problems)
3. [The bundle at a glance](#3-the-bundle-at-a-glance)
4. [The pipeline](#4-the-pipeline)
5. [Step 0: finding the tools](#5-step-0-finding-the-tools)
6. [Step 1: building the front end](#6-step-1-building-the-front-end)
7. [Step 2: adding the Qt runtime](#7-step-2-adding-the-qt-runtime)
8. [Step 3: adding the Python code](#8-step-3-adding-the-python-code)
9. [Step 4: adding a private Python and its packages](#9-step-4-adding-a-private-python-and-its-packages)
10. [Step 5: testing the bundle](#10-step-5-testing-the-bundle)
11. [Step 6: the archive](#11-step-6-the-archive)
12. [At run time: how the two halves find each other](#12-at-run-time-how-the-two-halves-find-each-other)
13. [Adapting the script to a real application](#13-adapting-the-script-to-a-real-application)
14. [Continuous integration](#14-continuous-integration)
15. [Troubleshooting](#15-troubleshooting)
16. [Platform notes and test matrix](#16-platform-notes-and-test-matrix)
17. [Appendix: the exact commands](#17-appendix-the-exact-commands)

---

## 1. The goal

A customer receives one folder (or one archive), opens it and starts the application. The machine
has **no Qt, no Python, no pip packages, no Visual C++ Redistributable** and the user has no
administrator rights. The application must still work exactly as it did on the developer's PC.

"Standalone" therefore means two promises:

| Promise | How the script keeps it | How it is checked |
|---|---|---|
| **Nothing has to be installed** on the target machine | every library, plugin, QML module, interpreter and Python package the application needs is copied into the folder | step 5b: a dependency scan of every program file in the bundle |
| **Nothing on the target machine is used**, even if it happens to be there | the front end looks for its Python *inside the bundle first*; Qt is loaded from the bundle by the operating system's own loader rules | step 5c: the packaged app runs its self-test with the build PC's `PATH` reduced to the operating system |

A bundle that only *happens* to work on the developer's PC (because Qt is on `PATH`, because the
VC++ runtime is installed, because numpy is in the global site-packages) fails the second promise.
The script is written so that such a bundle fails its own tests instead of failing at the customer.

## 2. Two halves, two problems

The application is two programs that talk to each other over a pipe
(see the README for the protocol). They are packaged by different means because they are
different kinds of program:

| | Front end `QtPythonDemo` | Back end `backend.py` |
|---|---|---|
| What it is | compiled C++ executable | source code for an interpreter |
| Needs at run time | the Qt shared libraries, Qt plugins, the QML modules it imports, the C++ runtime of its compiler | a CPython interpreter, the standard library, the packages from `requirements.txt`, and the native libraries those packages were built against |
| Produced by | CMake + the compiler of the Qt kit | nothing: the `.py` files are the program |
| Made standalone by | Qt's own deployment tools (`windeployqt` / the CMake deploy API) | shipping a complete private interpreter next to the code |
| Found at run time by | the OS loader: DLLs next to the exe (Windows), `RPATH` + `qt.conf` (Linux) | `Backend::locate()` in `frontend/backend.cpp` |

The important consequence: **the front end is packaged by copying what the executable links
against; the back end is packaged by copying an interpreter and installing packages into it.**
Everything in the script follows from that.

## 3. The bundle at a glance

What `dist/QtPythonDemo/` contains after a successful run, and which step put it there.

**Windows** (exe in the root, Qt DLLs next to it):

```
dist/QtPythonDemo/
├── QtPythonDemo.exe                      step 1   our front end (CMake, Release)
├── Qt6Core.dll  Qt6Gui.dll  Qt6Qml.dll  Qt6Quick.dll ...
│                                         step 2   Qt libraries              (windeployqt)
├── libstdc++-6.dll  libgcc_s_seh-1.dll  libwinpthread-1.dll
│                                         step 2   MinGW C++ runtime         (windeployqt --compiler-runtime)
├── platforms/qwindows.dll                step 2   the Windows platform plugin
├── imageformats/  iconengines/  tls/  networkinformation/  generic/  styles/
│                                         step 2   other Qt plugins the libraries can load
├── qml/QtQuick/  qml/QtQuick/Controls/  qml/QtQuick/Layouts/  qml/QtQml/ ...
│                                         step 2   the QML modules Main.qml imports (windeployqt --qmldir)
├── backend/
│   ├── backend.py                        step 3   our Python code
│   ├── requirements.txt                  step 3
│   └── python/                           step 4   a complete private CPython
│       ├── python.exe  python313.dll  python313.zip  python313._pth  *.pyd
│       ├── msvcp140.dll  vcruntime140.dll  vcruntime140_1.dll ...     VC++ runtime (from System32)
│       └── Lib/site-packages/numpy/ ...                                pip install --target
└── README.txt                            step 6
```

**Linux** (standard Unix layout produced by Qt's deploy API; `backend/` sits next to `bin/`):

```
dist/QtPythonDemo/
├── bin/
│   ├── QtPythonDemo                      step 1   our front end, RPATH = $ORIGIN/../lib
│   └── qt.conf                           step 2   "[Paths] Prefix=.."  → plugins/ and qml/ below
├── lib/
│   ├── libQt6Core.so.6  libQt6Gui.so.6  libQt6Qml.so.6  libQt6Quick.so.6 ...
│   ├── libQt6XcbQpa.so.6  libQt6DBus.so.6 ...
│   └── libicui18n.so.73  libicuuc.so.73  libicudata.so.73
│                                         step 2   Qt libraries + ICU         (cmake --install)
├── plugins/platforms/libqxcb.so            step 2   the X11 platform plugin   (cmake --install)
├── plugins/platforms/libqoffscreen.so  libqminimal.so
│                                         step 2   headless plugins, added by the script (CI, self-test)
├── plugins/imageformats/  iconengines/  xcbglintegrations/  egldeviceintegrations/  tls/ ...
├── qml/QtQuick/  qml/QtQuick/Controls/  qml/QtQuick/Layouts/  qml/QtQml/ ...
│                                         step 2   the QML modules Main.qml imports
├── backend/
│   ├── backend.py  requirements.txt      step 3
│   └── python/                           step 4   python-build-standalone (relocatable CPython)
│       ├── bin/python3  bin/python3.12
│       ├── lib/libpython3.12.so.1.0
│       └── lib/python3.12/              standard library
│           └── site-packages/numpy/     pip install --target
└── README.txt                            step 6
```

Nothing in either tree points outside the folder. That is why it can be copied anywhere,
to any user's home directory or a USB stick, and still start.

## 4. The pipeline

| Step | What | Tool | Input | Output | Typical time |
|---|---|---|---|---|---|
| 0 | find Qt, compiler, CMake, Ninja | `shutil.which`, well-known folders | `--qt`, `QTDIR`, `C:/Qt`, `~/Qt`, `/opt/Qt` | paths, build environment | instant |
| 1 | build the front end | CMake + the kit's compiler | `CMakeLists.txt`, `frontend/` | `build/package/QtPythonDemo(.exe)` | 20 s to 1 min |
| 2 | add the Qt runtime | Windows: `windeployqt` · Linux/macOS: `cmake --install` (Qt deploy API) | the executable | Qt libraries, plugins, QML modules, C++ runtime in `dist/` | 10 s |
| 3 | add the Python code | `shutil.copytree` | `backend/` | `dist/.../backend/` | instant |
| 4 | add a private Python + packages | python.org embeddable zip · python-build-standalone · `pip install --target` | `requirements.txt` | `backend/python/` | 30 s to 2 min (first run downloads ~11 MB / ~25 MB and the wheels) |
| 5 | test | the bundled Python, `tools/check_deps.py`, the packaged app `--selftest` | `dist/` | pass or a failing build | 10 s |
| 6 | archive | `zipfile` / `tarfile` | `dist/QtPythonDemo/` | `dist/QtPythonDemo-<os>-<cpu>.zip` or `.tar.gz` | 20 s |

Everything the script downloads goes to `build/cache/` and is reused on the next run, so after
the first run the script works offline. `build/package/` is an ordinary CMake build directory and
is reused as well, so rebuilds are incremental. `dist/` is deleted and recreated every time.

## 5. Step 0: finding the tools

The script needs four things and finds them in this order (`find_qt`, `toolchain`):

| Tool | Windows | Linux / macOS |
|---|---|---|
| **Qt kit** (the folder that holds `bin/`, `lib/cmake/Qt6/`) | `--qt`, then `QTDIR`, then the newest `C:/Qt/6.*/mingw_64` | `--qt`, then `QTDIR`, then the newest `~/Qt/6.*/gcc_64`, `/opt/Qt/6.*/gcc_64` (`.../macos` on macOS); if none is found the distribution's Qt is used (`apt install qt6-declarative-dev ...`), CMake finds it by itself |
| **Compiler** | the MinGW that came with the Qt installer: newest `C:/Qt/Tools/mingw*_64/bin` | the system `c++` (GCC or Clang) |
| **CMake** | `C:/Qt/Tools/CMake_64/bin/cmake.exe`, else `PATH` | `PATH` |
| **Ninja** | `C:/Qt/Tools/Ninja/ninja.exe`, else `PATH` (required) | `PATH`; falls back to `Unix Makefiles` if absent |

Why the kit's own compiler on Windows: a Qt built with MinGW 13 must be used with that MinGW. Mixing
in another g++ or MSVC gives link errors at best and a crashing executable at worst. The script
puts the compiler and `<kit>/bin` at the front of `PATH` for every command it runs, and passes the
compiler to CMake explicitly, so it does not matter what else is on the developer's `PATH`.

The script prints what it found before doing anything:

```
OS       : windows x86_64
Qt kit   : C:\Qt\6.9.2\mingw_64
Compiler : C:\Qt\Tools\mingw1310_64\bin
CMake    : C:\Qt\Tools\CMake_64\bin\cmake.exe (Ninja)
Python   : C:\Users\me\AppData\Local\Programs\Python\Python313\python.exe (3.13.14)
```

## 6. Step 1: building the front end

Two ordinary CMake commands, in `build/package/`:

```
cmake -S <repo> -B build/package -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=<kit> [compiler flags]
cmake --build build/package --parallel
```

* **`-DCMAKE_BUILD_TYPE=Release`**: a Release build links against the release Qt DLLs
  (`Qt6Core.dll`, not `Qt6Cored.dll`). `windeployqt --release` later copies exactly those. Shipping
  a Debug build would need the debug Qt libraries, which are several times larger and, with MinGW,
  far slower.
* **`-DCMAKE_PREFIX_PATH=<kit>`** is how CMake's `find_package(Qt6)` finds this particular Qt.
  Without it a different Qt on the machine could be picked up.
* **`WIN32_EXECUTABLE TRUE`** in `CMakeLists.txt` makes a GUI executable: no console window appears
  behind the application. The Python child never shows a console either, because Qt starts
  children of a GUI process with `CREATE_NO_WINDOW`.
* `target_compile_definitions(... DEMO_SOURCE_DIR=...)` bakes the source path into the binary.
  It is only used by the *development* fallback in `Backend::locate()`; in the bundle the first
  candidate (the private Python) wins before that path is ever looked at.

The result is a single file, `QtPythonDemo.exe` (about 150 kB). Everything else it needs is a
shared library, which is what step 2 collects.

## 7. Step 2: adding the Qt runtime

A Qt executable needs four groups of files that are not inside it:

1. **The Qt libraries** it links against (`Qt6Core`, `Qt6Gui`, `Qt6Qml`, `Qt6Quick`,
   `Qt6QuickControls2`, ...) and the libraries *those* link against (`Qt6Network`, `Qt6OpenGL`,
   ICU on Linux, ...).
2. **Qt plugins**, loaded at run time by name: the *platform plugin* (`qwindows` / `qxcb` /
   `qwayland`) without which no window can be created, image formats, icon engines, TLS back ends.
3. **QML modules**: every `import QtQuick.Controls` in a `.qml` file is resolved at run time to a
   folder under `qml/` that holds a plugin `.dll`/`.so`, a `qmldir` file and more `.qml` files.
4. **The compiler's C++ runtime**: `libstdc++-6.dll`, `libgcc_s_seh-1.dll`, `libwinpthread-1.dll`
   for MinGW. (On Linux the equivalent `libstdc++.so.6` is part of every distribution.)

Qt ships a tool that knows all four. The script uses it in the form that fits each platform.

### 7.1 Windows: `windeployqt`

```
windeployqt --release --qmldir frontend/qml --no-translations --compiler-runtime dist/QtPythonDemo/QtPythonDemo.exe
```

What it does, in order:

* Reads the **import table** of `QtPythonDemo.exe` (the list of DLLs the executable was linked
  against), follows the chain through the Qt DLLs, and copies every Qt library it finds next to the
  exe. Windows searches the executable's own folder first when loading a DLL, so a DLL next to the
  exe always wins over any Qt installed elsewhere.
* For each library, copies the **plugins** that library can load into their folders
  (`platforms/`, `imageformats/`, `tls/`, ...). Qt looks for plugins relative to the executable,
  so no `qt.conf` is needed on Windows.
* **`--qmldir frontend/qml`**: scans our `.qml` files for `import` statements, resolves them
  against the kit's `qml/` folder and copies only the modules actually imported (plus their
  dependencies) into `dist/qml/`. Without this flag no QML module would be copied and the
  application would start to a black window with "module QtQuick is not installed".
* **`--compiler-runtime`**: adds the MinGW runtime DLLs. They are not Qt, so `windeployqt` only
  copies them when asked. Forgetting this gives "libstdc++-6.dll was not found" on a clean PC, one
  of the most common packaging mistakes.
* **`--no-translations`**: leaves out Qt's own `.qm` translation files (a few MB) that this
  application does not use.
* **`--release`**: the exe was built in Release; copy the release libraries only. (`windeployqt`
  can usually tell, but with MinGW the detection sometimes picks the debug set; saying it explicitly
  costs nothing.)

### 7.2 Linux and macOS: Qt's CMake deployment API

Qt has no `linuxdeployqt`. Since Qt 6.5 the same job is done by CMake at *install* time. Four
lines in `CMakeLists.txt` set it up:

```cmake
install(TARGETS QtPythonDemo BUNDLE DESTINATION . RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
qt_generate_deploy_qml_app_script(TARGET QtPythonDemo OUTPUT_SCRIPT deploy_script
                                  NO_UNSUPPORTED_PLATFORM_ERROR NO_TRANSLATIONS)
install(SCRIPT ${deploy_script})
```

and the packaging script runs

```
cmake --install build/package --prefix dist/QtPythonDemo
```

The generated deploy script calls Qt's `qt_deploy_runtime_dependencies` /
`qt_deploy_qml_imports`, which:

* installs the executable to `bin/`;
* copies the Qt shared libraries it needs (resolved through the ELF `NEEDED` entries, the Linux
  equivalent of the import table) to `lib/`, including ICU;
* copies the plugins to `plugins/` and the imported QML modules to `qml/` (the QML import
  information comes from `qt_add_qml_module`, so there is no `--qmldir` to pass);
* **rewrites the RPATH** of the installed executable and libraries to `$ORIGIN/../lib`, so the
  dynamic loader finds the bundled libraries relative to the file that needs them, wherever the
  folder is moved;
* writes **`bin/qt.conf`** with `Prefix=..`, so Qt finds `plugins/` and `qml/` relative to the
  executable. (The script writes the file itself if the deploy script of a given Qt version does
  not.)

The deploy script copies only the platform plugin a desktop session needs, `libqxcb.so`. The
packaging script adds `libqoffscreen.so` and `libqminimal.so` from the kit, so the same bundle
also runs where there is no display (CI, SSH, the self-test in step 5) with
`QT_QPA_PLATFORM=offscreen`. Wayland desktops run the xcb plugin through XWayland, which every
Wayland compositor provides.

On macOS the same CMake code produces a `QtPythonDemo.app` through `macdeployqt`.

**What is deliberately not copied on Linux:** the operating system's own libraries, i.e. glibc,
`libstdc++`, X11/xcb, OpenGL (Mesa), fontconfig, freetype, glib, D-Bus. Qt's deploy script excludes
them on purpose, because a library like `libGL` must match the *target's* graphics driver, and
glibc is never shipped. Every desktop Linux installation has them. The one that is sometimes
missing on older systems is `libxcb-cursor0`, which Qt 6.5+'s xcb plugin requires; step 5b prints
the complete list of system libraries the bundle relies on.

### 7.3 Why not copy the Qt folder by hand?

Because the list is long, changes with every `import` in a `.qml` file, and differs between Qt
versions. `windeployqt` and the deploy script read the real dependencies of the real binaries; a
hand-made list is right until the next feature is added.

## 8. Step 3: adding the Python code

```python
shutil.copytree("backend", dist / "backend", ignore=ignore_patterns("__pycache__", "*.pyc", "python"))
```

The whole `backend/` folder is copied: `backend.py`, `requirements.txt` and anything a real
project adds there (packages, models, configuration files). Three things are left out:

* `__pycache__/` and `*.pyc`: compiled bytecode of the developer's Python version; useless for the
  bundled one and regenerated anyway (the front end sets `PYTHONDONTWRITEBYTECODE=1`).
* `backend/python/`: if a developer ran the bundled Python out of the source tree, that folder
  must not be copied into itself. Step 4 creates it fresh.

## 9. Step 4: adding a private Python and its packages

This is the step that makes the back end standalone and the one with the most to explain.

### 9.1 Why a private interpreter

The alternatives and why they were not chosen:

| Option | Problem |
|---|---|
| Use the Python installed on the target machine | there may be none; if there is, it is the wrong version or lacks the packages; the customer must not have to install anything |
| PyInstaller / Nuitka the back end into `backend.exe` | works for small pure-Python back ends, but repacks the interpreter and every package into an archive that is unpacked at start-up. Large scientific packages (numpy, scipy, torch, CUDA) frequently break or need hand-written hooks, debugging happens inside a repacked tree, and start-up is slower. For a back end that is *itself* a process behind a pipe, nothing is gained by hiding the interpreter |
| **A complete, relocatable CPython inside the bundle** (chosen) | the back end runs exactly as in development: same interpreter, same `site-packages`, same import behaviour. Debugging a bundle means running `backend/python/python.exe backend/backend.py`. The price is size: about 11 MB for the interpreter plus the packages |

The remaining question is where to get a CPython that works from any folder.

### 9.2 Windows: the python.org "embeddable" zip

python.org publishes, for every release, a `python-<version>-embed-amd64.zip`. It is the
official interpreter in a form meant for exactly this use: embedding in another application.
The script downloads it from `https://www.python.org/ftp/python/<ver>/python-<ver>-embed-amd64.zip`
(cached in `build/cache/`) and unzips it to `backend/python/`.

Contents: `python.exe`, `python313.dll`, `python313.zip` (the standard library as a zip),
the compiled extension modules (`_ssl.pyd`, `_socket.pyd`, `sqlite3.dll`, ...),
`vcruntime140.dll` and one small but decisive file: **`python313._pth`**.

An embeddable Python is *isolated by design*: it ignores `PYTHONPATH`, `PYTHONHOME`, the registry
and the user's site-packages. Its `sys.path` is exactly the lines of `python313._pth`. The
script writes:

```
python313.zip        the standard library
.                    the python folder itself (extension modules)
Lib\site-packages    where pip will put the packages (step 9.4)
import site          process .pth files inside site-packages (some packages need it)
```

The default file has no `site-packages` line and `import site` commented out, which is why
"I unzipped the embeddable Python and pip-installed into it, but `import numpy` fails" is such a
common report. With these four lines the private Python finds its packages and nothing else, so
a Python installed on the target machine, with conflicting packages, is never seen.

The version is the version of the Python running the script, because that is the simplest
guarantee that the wheels pip resolves in 9.4 exist for it.

### 9.3 Linux and macOS: python-build-standalone

python.org publishes no embeddable build for Linux, and a distribution's `/usr/bin/python3`
cannot be copied: it is compiled with absolute paths to `/usr/lib/python3.x`. The de-facto standard
relocatable CPython is **python-build-standalone**
(https://github.com/astral-sh/python-build-standalone), the builds that `uv`, `rye`, `pyenv`,
`pipx` and several IDEs download for their users. They are compiled so that the interpreter finds
its own `lib/` relative to the executable, and they run on any glibc 2.17 or newer system (RHEL 7,
Ubuntu 14.04 and everything after).

The script:

1. asks the GitHub API for the newest release (`.../releases/latest`, cached), or uses the
   `--python-url` you pass;
2. picks the asset `cpython-<version>+<date>-<cpu>-unknown-linux-gnu-install_only_stripped.tar.gz`,
   preferring the same `major.minor` as the Python running the script (`install_only` = the
   layout of a normal install, no build artefacts; `stripped` = without debug symbols, about 25 MB
   instead of 70 MB);
3. extracts it; the tarball holds one folder, `python/`, which becomes `backend/python/`
   (`bin/python3`, `lib/libpython3.x.so`, `lib/python3.x/`);
4. deletes the standard library's own test suite, IDLE and the turtle demos (`lib/python3.x/test`,
   `idlelib`, `turtledemo`, about 40 MB that no application needs);
5. asks that interpreter for its exact version, which pip needs in 9.4.

### 9.4 Installing the packages for *another* interpreter

The packages must go into the bundled Python's `site-packages`, but the bundled Python is not the
one running the script, and on Windows it does not even have pip. pip can install for a foreign
interpreter when told exactly what that interpreter is:

```
python -m pip install --only-binary=:all:
                      --python-version 3.13.14  --implementation cp
                      --platform win_amd64                       (Windows)
                      --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64   (Linux)
                      --target  backend/python/Lib/site-packages
                      -r backend/requirements.txt
```

| Flag | Meaning |
|---|---|
| `--target DIR` | unpack the wheels into `DIR` instead of the running Python's site-packages |
| `--python-version`, `--implementation cp`, `--platform` | describe the interpreter the wheels are *for*; pip picks wheels whose tags match (e.g. `numpy-2.3.4-cp313-cp313-win_amd64.whl`) instead of the running interpreter's tags |
| `--only-binary=:all:` | required with the flags above and the right thing anyway: an embeddable / standalone Python has no compiler and no headers, so a package that only exists as source could not be built for it. If a package in `requirements.txt` has no wheel, pip stops here with a clear message instead of producing a bundle that fails at the customer |
| `--cache-dir build/cache/pip` | keeps the downloaded wheels with the other cached files |

On Linux two `--platform` tags are given because wheels are tagged with the oldest glibc they
support (`manylinux_2_17` = glibc 2.17, `manylinux_2_28` = glibc 2.28); listing both accepts
either kind. The bundle then runs on any distribution with glibc 2.28 or newer
(Ubuntu 20.04, Debian 10, RHEL 8 and later) when a `2_28` wheel was chosen, older when not.

### 9.5 Windows only: the Visual C++ runtime

Every compiled Python package on Windows (numpy, pandas, torch, ...) is built with Microsoft's
compiler and needs the **Visual C++ runtime**: `vcruntime140.dll`, `vcruntime140_1.dll` and, for
C++ code, `msvcp140.dll`. Developers never notice because Visual Studio, Office or any game
installed the "VC++ Redistributable" long ago. A fresh Windows does not have `msvcp140.dll`, and
the embeddable zip brings `vcruntime140.dll` but not `msvcp140.dll`. The symptom is
`ImportError: DLL load failed while importing _multiarray_umath` on the customer's PC only.

The script copies `msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `vcruntime140.dll`,
`vcruntime140_1.dll`, `vcomp140.dll` and `concrt140.dll` from the build PC's `System32` into
`backend/python/`, next to `python.exe`. Windows searches the executable's folder first, so the
bundled copies are used whether or not the target has the redistributable. These DLLs are
redistributable by licence (that is what the name means).

## 10. Step 5: testing the bundle

A bundle is built once and copied many times; a mistake is expensive. Three tests run on every
build, each covering what the previous one cannot.

### 10.1 Test a: the bundled Python answers a request

```
backend/python/python.exe -u backend/backend.py   <  {"id":1,"method":"stats","params":{"values":[1,2,3,4]}}
```

The private interpreter, started from the bundle with its own `site-packages`, must import numpy
and answer with `"mean": 2.5`. This catches a wrong `._pth`, a missing package, a wheel for the
wrong platform, and (on Windows) a missing VC++ DLL, because numpy's import would fail. It does not
yet involve the front end.

### 10.2 Test b: every library the bundle needs is inside it (`tools/check_deps.py`)

The test that keeps promise 1. It does what the operating system's loader will do on the target
machine, for *every* program file in the bundle, without running anything:

* **Windows**: reads the import table of every `.exe`, `.dll` and `.pyd` (a 60-line PE parser,
  no third-party module) and checks that each imported DLL is found where Windows would look:
  next to the importing file, next to the executable that will load it (the bundle root for the
  Qt app, `backend/python/` for everything under it), or in the folders a package adds with
  `os.add_dll_directory` (`numpy.libs/`, `torch/lib/`). DLLs that are part of every Windows
  installation (`kernel32`, `user32`, `api-ms-win-*`, ...) are allowed; anything else that is not
  found is reported with the files that need it, and the build fails.
* **Linux**: runs `ldd` on every ELF file (the executable, every `.so` under `lib/`, `plugins/`,
  `qml/` and `site-packages/`). `ldd` applies the real RPATH rules, so it verifies that the
  rewritten RPATHs of step 7.2 work. Any `not found` fails the build. Libraries resolved from the
  system (`libc`, `libGL`, `libxcb-*`, ...) are printed as a list: that is what the target
  distribution must provide.

This is the check that turns "it works on my PC" into "it works on a PC that has nothing".

### 10.3 Test c: the packaged application's self-test

```
dist/QtPythonDemo/QtPythonDemo.exe --selftest        (PATH = %SystemRoot%\System32;%SystemRoot%)
dist/QtPythonDemo/bin/QtPythonDemo  --selftest        (PATH = /usr/bin:/bin, QT_QPA_PLATFORM=offscreen if no display)
```

The real front end, from the bundle, with `PATH` cut down to the operating system and the Qt and
Python environment variables (`QT_PLUGIN_PATH`, `PYTHONPATH`, `LD_LIBRARY_PATH`, ...) removed.
`--selftest` (see `frontend/main.cpp`) starts the back end exactly as the GUI would, calls
`add(2, 3)` and exits with:

| Exit code | Meaning |
|---|---|
| 0 | the front end loaded Qt from the bundle, found the private Python, started it, received `ready`, sent a request and got `5` back |
| 1 | the back end could not be found or failed (its stderr is printed) |
| 2 | no answer within 15 seconds |

This is the test that keeps promise 2, end to end. On a Linux without a display (CI, SSH) Qt is
started with the `offscreen` platform plugin that step 2 added to the bundle; with a display the
real `xcb` plugin is used. The same command is what you run on the
customer's machine when something is wrong, before looking anywhere else.

## 11. Step 6: the archive

* **Windows**: `dist/QtPythonDemo-windows-x86_64.zip`, standard deflate. The folder is about
  170 MB, the zip about 64 MB (Qt's DLLs compress well).
* **Linux / macOS**: `dist/QtPythonDemo-linux-x86_64.tar.gz`. A zip would lose the executable
  bits of `bin/QtPythonDemo` and `bin/python3` and the symbolic links between
  `libQt6Core.so.6` and `libQt6Core.so.6.9.2`; tar keeps both. The Linux folder is about
  265 MB (the Qt libraries are larger than on Windows and ICU alone is 31 MB), the
  archive about 90 MB.

The archive contains the folder itself (`QtPythonDemo/...`), so unpacking it anywhere yields the
same tree as `dist/QtPythonDemo/`. Also written: `README.txt` inside the bundle with the one
line the end user needs and the one command to run when it does not work.

## 12. At run time: how the two halves find each other

### 12.1 Finding Qt

Nothing in our code: the operating system does it. On Windows the loader searches the
executable's folder before anything else, and Qt looks for plugins and QML modules relative to the
executable. On Linux the RPATH `$ORIGIN/../lib` written in step 7.2 points the loader at the
bundled `lib/`, and `bin/qt.conf` points Qt at `plugins/` and `qml/`. A Qt installed on the target
machine, even a different version on `PATH` or `LD_LIBRARY_PATH`, is not consulted first.

### 12.2 Finding Python: `Backend::locate()`

The front end tries, in this order, and takes the first that exists:

| # | Windows | Linux | When |
|---|---|---|---|
| 1 | `<exe dir>/backend/python/python.exe` + `backend/backend.py` | `<exe dir>/../backend/python/bin/python3` + `backend/backend.py` | **the bundle** |
| 2 | `python` or `python3` from `PATH` + `<bundle>/backend/backend.py` | `python3` or `python` from `PATH` + the same | a bundle built without step 4 (a developer copy) |
| 3 | `python` from `PATH` + `<source dir>/backend/backend.py` | same | **development** in Qt Creator; the source path was baked in by CMake |

`<exe dir>`, `<exe dir>/..` and `<exe dir>/../Resources` are all tried, which covers the three
layouts (Windows root, Linux `bin/`, macOS `.app`). Because the bundle's own Python is candidate 1,
a Python on the target machine is never used by a complete bundle.

### 12.3 The child process

`QProcess` starts the interpreter with `-u` and the environment variables `PYTHONUNBUFFERED=1`
(no answer may wait in a buffer), `PYTHONIOENCODING=utf-8` (JSON with any text, regardless of the
machine's code page) and `PYTHONDONTWRITEBYTECODE=1` (no `__pycache__` written into the bundle,
which may be read-only). The working directory is the `backend/` folder, so relative paths in the
Python code (data files, models) work the same in development and in the bundle. On exit the front
end closes the child's stdin; `backend.py` returns from its `for line in sys.stdin` loop and ends
by itself, with a kill after two seconds as a last resort.

## 13. Adapting the script to a real application

The script is deliberately short so that it can be read in full and changed. For a real project:

1. **Name**: change `APP = "QtPythonDemo"` and the target name in `CMakeLists.txt`. Everything
   else derives from them.
2. **Python code**: put your package under `backend/`, import it in `backend.py`, add one line
   per function to `METHODS`. Step 3 copies the whole folder.
3. **Packages**: list them in `backend/requirements.txt` **with pinned versions**
   (`numpy==2.3.4`). Pinning is what makes two bundles built a month apart identical.
4. **Large data** (models, weights): either commit them under `backend/`, or add a download step
   to `package.py` before step 3 that fetches them into `build/cache/` and copies them into
   `backend/`. Do not rely on the developer having them in some other folder.
5. **Large packages** (torch, CUDA): nothing changes in principle; expect a multi-GB bundle, and
   read test b's output. Some packages need extra DLLs that step 9.5 does not cover
   (`VCRUNTIME140_THREADS.dll`, `MSVCP140_ATOMIC_WAIT.dll`): add them to the list in
   `add_vc_runtime`, next to the existing names.
6. **Extra Qt modules** (`Qt6Charts`, `Qt6Multimedia`, ...): add them to `find_package` and
   `target_link_libraries`; `windeployqt` and the deploy script follow the link dependencies
   automatically. New `import`s in QML are picked up through `--qmldir` / the QML module.
7. **Size**: the Qt part can be trimmed. `opengl32sw.dll` (20 MB, software OpenGL fallback) can
   go when every target has a GPU driver; QML style folders you do not use (`qml/QtQuick/Controls/Material`,
   `Imagine`, `Universal`) can be deleted after step 2. Do it in the script, not by hand, so it
   happens every time.
8. **Code signing**: an unsigned `.exe` triggers SmartScreen on a new PC. Sign
   `dist/<App>/<App>.exe` with `signtool` after step 2 (and the installer, if you make one) using
   the company certificate. Add it as a step; never sign by hand.
9. **Installer**: optional. The folder *is* the deliverable. If an installer is wanted, point
   Inno Setup or WiX at `dist/<App>/` as the source tree; the installer only copies the folder and
   creates a shortcut.
10. **Version stamp**: write `git describe` into a `VERSION` file in step 3 and show it in the UI's
    "About"; when a customer reports a problem you know exactly which bundle they have.

## 14. Continuous integration

Because everything is one command and the checks are built in, a pipeline is small. GitHub Actions,
building both platforms on every tag:

```yaml
name: bundle
on:
  push:
    tags: ["v*"]
jobs:
  build:
    strategy:
      matrix:
        include:
          - os: windows-latest
            qt_arch: win64_mingw
            qt_tools: tools_mingw1310 tools_ninja tools_cmake
          - os: ubuntu-22.04
            qt_arch: linux_gcc_64
            qt_tools: ""
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - uses: jurplel/install-qt-action@v4
        with:
          version: "6.9.2"
          arch: ${{ matrix.qt_arch }}
          tools: ${{ matrix.qt_tools }}
      - if: runner.os == 'Linux'
        run: sudo apt-get install -y ninja-build libgl-dev libegl-dev libxkbcommon-dev libxcb-cursor0
      - run: python tools/package.py --qt "$QT_ROOT_DIR"
        shell: bash
      - uses: actions/upload-artifact@v4
        with:
          name: bundle-${{ matrix.os }}
          path: |
            dist/*.zip
            dist/*.tar.gz
```

`install-qt-action` sets `QT_ROOT_DIR` to the kit folder. The three tests of step 5 run inside the
job, so a bundle that would fail at a customer fails the pipeline. `ubuntu-22.04` rather than
`latest` keeps the bundle's glibc requirement low (the exe is linked against the builder's glibc;
a Linux bundle runs on distributions as new as or newer than the one it was built on).

## 15. Troubleshooting

| Symptom | Where | Cause and fix |
|---|---|---|
| `Qt 6 MinGW kit not found` | step 0 | pass `--qt C:/Qt/<ver>/mingw_64`, or set `QTDIR` |
| `CMake was unable to find a build program corresponding to "Ninja"` | step 1 | Ninja is not under `C:/Qt/Tools/Ninja` and not on `PATH`: install it from the Qt installer (*Tools*) |
| `Could not find a package configuration file provided by "Qt6"` | step 1 | wrong `--qt` (must be the folder that holds `lib/cmake/Qt6`), or on Linux no Qt dev packages installed |
| `ld: cannot open output file QtPythonDemo: Is a directory` | step 1, Linux | the QML module's output folder has the same name as the executable; `CMakeLists.txt` sets `QT_QML_OUTPUT_DIRECTORY` to `<build>/qml` to avoid it. Keep that line when you rename the project |
| `Could not find the Qt platform plugin "offscreen"` | test c, headless Linux | the bundle has only `libqxcb.so`; the script copies `libqoffscreen.so` from the kit's `plugins/platforms`. With a distro Qt install `qt6-qpa-plugins` first |
| `module "QtQuick.Controls" is not installed` on the target | run time | `qml/` is incomplete: a QML import was added after the last `package.py` run, or the files were copied without the `qml/` folder. Rebuild the bundle |
| `libstdc++-6.dll was not found` | target | `windeployqt` ran without `--compiler-runtime`; the script always passes it, so the bundle was not produced by the script |
| `ImportError: DLL load failed while importing _multiarray_umath` | target, Windows | a VC++ runtime DLL is missing: `tools/check_deps.py dist/QtPythonDemo` names it; add it in `add_vc_runtime` |
| `ModuleNotFoundError: No module named 'numpy'` in the bundle only | test a | the package is not in `requirements.txt`, or the `._pth` file lost its `Lib\site-packages` line |
| `ERROR: No matching distribution found for <pkg>` with `--only-binary` | step 4 | the package has no wheel for the bundled Python version / platform: pin a version that has one, or use a Python version that it supports (run the script with that Python) |
| `FAIL: ... libraries are not found anywhere` | test b, Linux | a library the Qt or Python side needs is neither in the bundle nor on the system: install the `-dev`/runtime package on the build machine and rebuild, or copy it into `lib/` in step 2 |
| self-test exit 1, log says `no Python back end found` | test c | `backend/python/` is missing or at the wrong level: Windows next to the exe, Linux next to `bin/` |
| self-test exit 2 | test c | the back end started but never printed `ready`: run it by hand (`backend/python/python.exe -u backend/backend.py`) and read stderr |
| `Could not load the Qt platform plugin "xcb"` on the target | target, Linux | a system library of the xcb plugin is missing, almost always `libxcb-cursor0`: `apt install libxcb-cursor0`. `QT_DEBUG_PLUGINS=1 bin/QtPythonDemo` prints which one |
| SmartScreen / antivirus warning | target, Windows | unsigned executable; sign it (section 13, item 8) |
| Bundle starts on the build PC but not on the target | anywhere | the bundle was copied incompletely, or a test was skipped. Run `QtPythonDemo --selftest` on the target first: its exit code says which half failed |

## 16. Platform notes and test matrix

| Platform | Build tools | Private Python | Qt runtime | Verified |
|---|---|---|---|---|
| Windows 10/11 x86_64 | Qt 6.9.2 MinGW 13.1, CMake/Ninja from the Qt installer, Python 3.13 | python.org embeddable zip 3.13 | `windeployqt` | yes: Windows 11, full run of the script, self-test with `PATH` reduced to Windows |
| Linux x86_64 | Qt 6.9.2 `gcc_64` (online installer), GCC 13, CMake 3.28, Python 3.12 | python-build-standalone 3.12.16 | `cmake --install` (Qt deploy API) | yes: Ubuntu 24.04 (WSL 2), full run of the script; `ldd` check clean; self-test passed both with a display (`xcb`, WSLg) and headless (`offscreen`, empty environment) |
| Linux with a distro Qt (`qt6-declarative-dev`) | distro GCC/CMake | same | same | expected to work with Qt 6.5+; not run here |
| macOS | Qt 6.5+ `macos` kit, Xcode tools | python-build-standalone | `macdeployqt` via the same CMake deploy API | **not run**; the code paths exist (`.app` layout, `Contents/Resources/backend`), expect to adjust |
| Windows / Linux on ARM64 | same tools for the architecture | `embed-arm64.zip` / `aarch64` builds | same | not run; the script picks the right downloads by `platform.machine()` |

A Linux bundle built on one distribution runs on distributions of the same age or newer (glibc is
backward compatible, not forward). Build on the oldest distribution you need to support.

## 17. Appendix: the exact commands

What the script runs, for someone who wants to reproduce one step by hand.

**Windows**

```bat
:: 1 build
C:\Qt\Tools\CMake_64\bin\cmake.exe -S . -B build\package -G Ninja -DCMAKE_MAKE_PROGRAM=C:\Qt\Tools\Ninja\ninja.exe ^
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=C:\Qt\6.9.2\mingw_64 ^
    -DCMAKE_C_COMPILER=C:\Qt\Tools\mingw1310_64\bin\gcc.exe -DCMAKE_CXX_COMPILER=C:\Qt\Tools\mingw1310_64\bin\g++.exe
cmake --build build\package --parallel
:: 2 Qt runtime
copy build\package\QtPythonDemo.exe dist\QtPythonDemo\
C:\Qt\6.9.2\mingw_64\bin\windeployqt.exe --release --qmldir frontend\qml --no-translations --compiler-runtime dist\QtPythonDemo\QtPythonDemo.exe
:: 3 Python code
xcopy /E /I backend dist\QtPythonDemo\backend
:: 4 private Python
curl -LO https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip
tar -xf python-3.13.14-embed-amd64.zip -C dist\QtPythonDemo\backend\python
(echo python313.zip& echo .& echo Lib\site-packages& echo import site) > dist\QtPythonDemo\backend\python\python313._pth
python -m pip install --only-binary=:all: --python-version 3.13.14 --implementation cp --platform win_amd64 ^
    --target dist\QtPythonDemo\backend\python\Lib\site-packages -r backend\requirements.txt
copy %SystemRoot%\System32\msvcp140.dll dist\QtPythonDemo\backend\python\
:: 5 tests
echo {"id":1,"method":"stats","params":{"values":[1,2,3,4]}} | dist\QtPythonDemo\backend\python\python.exe -u dist\QtPythonDemo\backend\backend.py
python tools\check_deps.py dist\QtPythonDemo
dist\QtPythonDemo\QtPythonDemo.exe --selftest & echo %errorlevel%
```

**Linux**

```sh
# 1 build
cmake -S . -B build/package -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=~/Qt/6.9.2/gcc_64
cmake --build build/package --parallel
# 2 Qt runtime (runs the deploy script generated from CMakeLists.txt)
cmake --install build/package --prefix dist/QtPythonDemo
# 3 Python code
cp -r backend dist/QtPythonDemo/backend
# 4 private Python
curl -LO https://github.com/astral-sh/python-build-standalone/releases/download/<tag>/cpython-3.12.<x>+<tag>-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
tar -xzf cpython-*.tar.gz && mv python dist/QtPythonDemo/backend/python
python3 -m pip install --only-binary=:all: --python-version 3.12.<x> --implementation cp \
    --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 \
    --target dist/QtPythonDemo/backend/python/lib/python3.12/site-packages -r backend/requirements.txt
# 5 tests
echo '{"id":1,"method":"stats","params":{"values":[1,2,3,4]}}' | dist/QtPythonDemo/backend/python/bin/python3 -u dist/QtPythonDemo/backend/backend.py
python3 tools/check_deps.py dist/QtPythonDemo
QT_QPA_PLATFORM=offscreen dist/QtPythonDemo/bin/QtPythonDemo --selftest; echo $?
# 6 archive
tar -C dist -czf dist/QtPythonDemo-linux-x86_64.tar.gz QtPythonDemo
```

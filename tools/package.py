"""
package.py - build the standalone Windows bundle of QtPythonDemo
=================================================================
One command:

    python tools/package.py            (add --qt C:/Qt/6.9.2/mingw_64 if auto-detection fails)

Result:  dist/QtPythonDemo/            a folder that runs on any 64-bit Windows 10/11 PC
         dist/QtPythonDemo-windows-x86_64.zip

What the folder contains and where it comes from:

    QtPythonDemo.exe        our front end, built here with CMake (Release)
    Qt6*.dll, qml/, ...     the Qt runtime, copied by Qt's own tool  windeployqt
    libgcc_s_seh-1.dll ...  the MinGW C++ runtime, also copied by windeployqt
    backend/backend.py      our Python code, copied as is
    backend/python/         a complete private Python: the official "embeddable"
                            CPython zip from python.org (same version as the
                            Python running this script) + the packages from
                            backend/requirements.txt installed into it with pip

Nothing is installed on the target PC and nothing on it is used: the front end
finds backend/python/python.exe next to itself (see frontend/backend.cpp).

Needs on the build PC: Qt 6 (MinGW 64-bit kit, with the CMake and Ninja that
the Qt installer puts under C:/Qt/Tools), Python 3.x with pip, and internet
access once (the embeddable zip and the wheels are cached in build/cache).
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = "QtPythonDemo"


def step(msg):
    print(f"\n== {msg}", flush=True)


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def newest(paths, key=None):
    paths = sorted(paths, key=key or (lambda p: p.name))
    return paths[-1] if paths else None


def find_qt(arg):
    """The Qt kit folder, e.g. C:/Qt/6.9.2/mingw_64 (holds bin/qmake.exe)."""
    for cand in (arg, os.environ.get("QTDIR")):
        if cand and (Path(cand) / "bin" / "windeployqt.exe").exists():
            return Path(cand)
    kits = [p for p in Path("C:/Qt").glob("6.*/mingw_64") if (p / "bin" / "windeployqt.exe").exists()]
    kit = newest(kits, key=lambda p: [int(x) for x in p.parent.name.split(".")])
    if not kit:
        raise SystemExit("Qt 6 MinGW kit not found: pass --qt C:/Qt/<version>/mingw_64")
    return kit


def find_tool(name, *extra_dirs):
    for d in extra_dirs:
        if d and (Path(d) / name).exists():
            return Path(d) / name
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found (install it with the Qt installer under C:/Qt/Tools, or put it on PATH)")
    return Path(found)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--qt", help="Qt kit folder, e.g. C:/Qt/6.9.2/mingw_64 (default: newest under C:/Qt)")
    ap.add_argument("--no-zip", action="store_true", help="leave out the .zip")
    args = ap.parse_args()

    qt = find_qt(args.qt)
    mingw = newest(Path("C:/Qt/Tools").glob("mingw*_64/bin"))
    cmake = find_tool("cmake.exe", "C:/Qt/Tools/CMake_64/bin")
    ninja = find_tool("ninja.exe", "C:/Qt/Tools/Ninja")
    gxx = find_tool("g++.exe", mingw)
    print(f"Qt kit : {qt}\nMinGW  : {gxx.parent}\nPython : {sys.executable} ({'.'.join(map(str, sys.version_info[:3]))})")

    build = ROOT / "build" / "package"
    cache = ROOT / "build" / "cache"
    dist = ROOT / "dist" / APP
    cache.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": f"{gxx.parent};{qt / 'bin'};{os.environ['PATH']}"}

    # ---------------------------------------------------------------- 1. front end
    step("1/5  build the Qt front end (Release)")
    build.mkdir(parents=True, exist_ok=True)
    run([cmake, "-S", ROOT, "-B", build, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}",
         "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_PREFIX_PATH={qt}",
         f"-DCMAKE_C_COMPILER={gxx.parent / 'gcc.exe'}", f"-DCMAKE_CXX_COMPILER={gxx}"], env=env)
    run([cmake, "--build", build, "--parallel"], env=env)

    step("2/5  copy the exe and let windeployqt add the Qt runtime")
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copy2(build / f"{APP}.exe", dist / f"{APP}.exe")
    # --qmldir: scan our QML to copy exactly the QML modules it imports
    run([qt / "bin" / "windeployqt.exe", "--release", "--qmldir", ROOT / "frontend" / "qml",
         "--no-translations", "--compiler-runtime", dist / f"{APP}.exe"], env=env, stdout=subprocess.DEVNULL)

    # ---------------------------------------------------------------- 2. back end
    step("3/5  copy the Python code")
    backend = dist / "backend"
    shutil.copytree(ROOT / "backend", backend, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "python"))

    ver = ".".join(map(str, sys.version_info[:3]))
    step(f"4/5  private Python {ver} (embeddable zip from python.org) + requirements.txt")
    zip_name = f"python-{ver}-embed-amd64.zip"
    zip_path = cache / zip_name
    if not zip_path.exists():
        url = f"https://www.python.org/ftp/python/{ver}/{zip_name}"
        print("  download", url, flush=True)
        urllib.request.urlretrieve(url, zip_path)
    pydir = backend / "python"
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(pydir)
    # The python3xx._pth file IS sys.path of an embeddable Python (PYTHONPATH is
    # ignored): the stdlib zip, the python folder, site-packages, and "import site"
    # so .pth files in site-packages work.
    pth = next(pydir.glob("python*._pth"))
    stdlib_zip = next(pydir.glob("python*.zip")).name
    pth.write_text(f"{stdlib_zip}\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    site = pydir / "Lib" / "site-packages"
    # Install the wheels for THAT Python (binary wheels only: an embeddable Python
    # cannot compile), with the pip of the Python running this script.
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
         "--python-version", ver, "--platform", "win_amd64", "--implementation", "cp",
         "--target", site, "--cache-dir", cache / "pip", "-r", ROOT / "backend" / "requirements.txt"])
    # Compiled packages (numpy ...) need the Visual C++ runtime; the embeddable zip
    # brings vcruntime140*.dll but not msvcp140.dll.  Take them from this PC so the
    # target PC does not need the VC++ Redistributable.
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll",
                 "vcomp140.dll", "concrt140.dll"):
        if not (pydir / name).exists() and (system32 / name).exists():
            shutil.copy2(system32 / name, pydir / name)

    # ---------------------------------------------------------------- 3. check
    step("5/5  smoke test: the bundled Python answers a request")
    req = b'{"id": 1, "method": "stats", "params": {"values": [1, 2, 3, 4]}}\n'
    out = subprocess.run([pydir / "python.exe", "-u", backend / "backend.py"], input=req, capture_output=True,
                         cwd=backend, timeout=60)
    print("  ", out.stdout.decode().strip().replace("\n", "\n   "))
    if out.returncode != 0 or b'"mean": 2.5' not in out.stdout:
        print(out.stderr.decode())
        raise SystemExit("the bundled back end does not work")

    # Every DLL that any .exe / .dll / .pyd in the bundle imports must be in the
    # bundle or be part of Windows: catches "it works on my PC only" before shipping.
    step("release check: no DLL from this PC is needed (tools/check_deps.py)")
    sys.path.insert(0, str(ROOT / "tools"))
    import check_deps  # noqa: E402
    if check_deps.main(["check_deps.py", str(dist)]) != 0:
        raise SystemExit("bundle incomplete: see the missing DLLs above")

    (dist / "README.txt").write_text(
        f"{APP} - Qt front end + Python back end, standalone\n\n"
        "Double-click QtPythonDemo.exe.  Nothing needs to be installed: the Qt runtime is in this\n"
        "folder and backend\\python\\ is a complete private Python with its packages.\n"
        "Keep the folder together; unzip to a local disk (not a network share).\n"
        "If the status says 'error', run  backend\\python\\python.exe backend\\backend.py  in a\n"
        "command prompt inside this folder and type a request such as\n"
        '{"id": 1, "method": "add", "params": {"a": 2, "b": 3}}  to see the Python error.\n',
        encoding="utf-8")

    size = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file())
    print(f"\nbundle: {dist}  ({size / 1e6:.0f} MB, {sum(1 for _ in dist.rglob('*'))} files)")
    if not args.no_zip:
        zip_out = dist.parent / f"{APP}-windows-x86_64.zip"
        with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(dist.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(dist.parent).as_posix())
        print(f"zip   : {zip_out}  ({zip_out.stat().st_size / 1e6:.0f} MB)")
    print("\nCopy the folder (or the zip) to another PC and double-click QtPythonDemo.exe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

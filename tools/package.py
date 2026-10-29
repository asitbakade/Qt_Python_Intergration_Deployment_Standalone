"""
package.py - build the standalone bundle of QtPythonDemo (Windows, Linux, macOS)
================================================================================
One command, run from anywhere:

    python tools/package.py                 Windows
    python3 tools/package.py                Linux / macOS
      --qt <kit>        the Qt kit folder, e.g. C:/Qt/6.9.2/mingw_64 or ~/Qt/6.9.2/gcc_64
                        (default: the newest one found; on Linux a distro Qt also works)
      --no-archive      leave out the .zip / .tar.gz
      --python-url      Linux/macOS: use this python-build-standalone tarball instead of the newest release

Result:  dist/QtPythonDemo/                        a folder that runs on a machine with no Qt and no Python
         dist/QtPythonDemo-<os>-<cpu>.zip|.tar.gz  the same folder, for sending

The six steps (docs/PACKAGING.md explains each one in detail):

    1  build the front end     CMake + the compiler of the Qt kit, Release
    2  add the Qt runtime      Windows: windeployqt      Linux/macOS: cmake --install (Qt's deploy API)
    3  add the Python code     copy backend/ (without caches)
    4  add a private Python    Windows: python.org "embeddable" zip   Linux/macOS: python-build-standalone
                               + `pip install --target` of backend/requirements.txt for THAT interpreter
    5  test the bundle         a) the bundled Python answers a request
                               b) every library the bundle needs is inside it (tools/check_deps.py)
                               c) the packaged app passes --selftest with nothing of this machine on PATH
    6  archive                 zip (Windows) or tar.gz (Linux/macOS)

Nothing is installed on the target machine and nothing on it is used: the
front end finds backend/python/ next to itself (frontend/backend.cpp, locate()).

Needs on the build machine: Qt 6.5+ with the kit's compiler, CMake 3.21+,
Python 3.10+ with pip, and internet access once (downloads are cached in build/cache).
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = "QtPythonDemo"

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS
OS_NAME = "windows" if WINDOWS else "macos" if MACOS else "linux"
MACHINE = platform.machine().lower()
ARM = MACHINE in ("arm64", "aarch64")
CPU = "arm64" if ARM else "x86_64"


# ----------------------------------------------------------------------------- helpers

def step(msg):
    print(f"\n== {msg}", flush=True)


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def newest(paths, key=None):
    paths = sorted(paths, key=key or (lambda p: p.name))
    return paths[-1] if paths else None


def version_key(kit):
    """~/Qt/6.9.2/gcc_64 -> [6, 9, 2], so that 6.10 sorts after 6.9."""
    try:
        return [int(x) for x in kit.parent.name.split(".")]
    except ValueError:
        return [0]


def download(url, dest):
    print("  download", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "QtPythonDemo-package.py"})
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# ----------------------------------------------------------------------------- tools

def find_qt(arg):
    """The Qt kit folder (holds bin/, lib/cmake/Qt6/...).  Windows: C:/Qt/<ver>/mingw_64,
    Linux: ~/Qt/<ver>/gcc_64, macOS: ~/Qt/<ver>/macos.  None = use the Qt that CMake finds
    on its own (a distro package such as qt6-declarative-dev); only allowed on Linux/macOS."""
    marker = Path("bin/windeployqt.exe") if WINDOWS else Path("lib/cmake/Qt6/Qt6Config.cmake")
    for cand in (arg, os.environ.get("QTDIR")):
        if cand and (Path(cand) / marker).exists():
            return Path(cand)
        if cand:
            raise SystemExit(f"{cand} is not a Qt kit folder ({marker} missing)")
    if WINDOWS:
        roots, kit = [Path("C:/Qt")], "mingw_64"
    elif MACOS:
        roots, kit = [Path.home() / "Qt", Path("/opt/Qt")], "macos"
    else:
        roots, kit = [Path.home() / "Qt", Path("/opt/Qt"), Path("/usr/local/Qt")], "gcc_64"
    kits = [p for r in roots for p in r.glob(f"6.*/{kit}") if (p / marker).exists()]
    found = newest(kits, key=version_key)
    if found:
        return found
    if WINDOWS:
        raise SystemExit("Qt 6 MinGW kit not found under C:/Qt: pass --qt C:/Qt/<version>/mingw_64")
    print("  no Qt kit under ~/Qt or /opt/Qt: using the Qt that CMake finds (distro packages)", flush=True)
    return None


def find_tool(name, *extra_dirs):
    for d in extra_dirs:
        if d and (Path(d) / name).exists():
            return Path(d) / name
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found: install it (Qt installer -> Tools, or your package manager) or put it on PATH")
    return Path(found)


def toolchain(qt):
    """cmake, generator, extra CMake arguments, environment and compiler name for the build."""
    env = dict(os.environ)
    if WINDOWS:
        mingw = newest(Path("C:/Qt/Tools").glob("mingw*_64/bin"))
        cmake = find_tool("cmake.exe", "C:/Qt/Tools/CMake_64/bin")
        ninja = find_tool("ninja.exe", "C:/Qt/Tools/Ninja")
        gxx = find_tool("g++.exe", mingw)
        env["PATH"] = f"{gxx.parent};{qt / 'bin'};{env['PATH']}"
        cmake_args = [f"-DCMAKE_MAKE_PROGRAM={ninja}", f"-DCMAKE_C_COMPILER={gxx.parent / 'gcc.exe'}",
                      f"-DCMAKE_CXX_COMPILER={gxx}"]
        compiler = str(gxx.parent)
    else:
        cmake = find_tool("cmake")
        ninja = shutil.which("ninja")
        cmake_args = []
        compiler = shutil.which("c++") or "c++ (from PATH)"
        if qt:
            env["PATH"] = f"{qt / 'bin'}:{env['PATH']}"
    generator = "Ninja" if ninja else "Unix Makefiles"
    return cmake, generator, cmake_args, env, compiler


# ----------------------------------------------------------------------------- private Python

def wheel_platforms():
    """pip --platform tags for the BUNDLED interpreter (not for the Python running this script)."""
    if WINDOWS:
        return ["win_arm64" if ARM else "win_amd64"]
    if MACOS:
        return ["macosx_11_0_arm64"] if ARM else ["macosx_10_13_x86_64"]
    arch = "aarch64" if ARM else "x86_64"
    return [f"manylinux_2_28_{arch}", f"manylinux_2_17_{arch}"]


def embeddable_python(cache, pydir):
    """Windows: the official python.org 'embeddable' zip, same version as the Python running
    this script.  Returns (python.exe, version, site-packages)."""
    ver = ".".join(map(str, sys.version_info[:3]))
    zip_name = f"python-{ver}-embed-{'arm64' if ARM else 'amd64'}.zip"
    zip_path = cache / zip_name
    if not zip_path.exists():
        download(f"https://www.python.org/ftp/python/{ver}/{zip_name}", zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(pydir)
    # python3xx._pth IS sys.path of an embeddable Python (PYTHONPATH and the
    # registry are ignored): the stdlib zip, the python folder, site-packages,
    # and "import site" so that .pth files inside site-packages are processed.
    pth = next(pydir.glob("python*._pth"))
    stdlib_zip = next(pydir.glob("python*.zip")).name
    pth.write_text(f"{stdlib_zip}\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    return pydir / "python.exe", ver, pydir / "Lib" / "site-packages"


def standalone_python(cache, pydir, url_override):
    """Linux/macOS: a relocatable CPython from python-build-standalone (the builds that uv,
    rye and pyenv ship).  Newest release; same major.minor as the Python running this
    script when the release has it.  Returns (bin/python3, version, site-packages)."""
    if MACOS:
        target = "aarch64-apple-darwin" if ARM else "x86_64-apple-darwin"
    else:
        target = "aarch64-unknown-linux-gnu" if ARM else "x86_64-unknown-linux-gnu"
    url = url_override
    if not url:
        info = cache / "python-build-standalone-latest.json"
        if not info.exists():      # cached: delete build/cache to pick up a newer release
            download("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest", info)
        release = json.loads(info.read_text(encoding="utf-8"))
        suffix = f"-{target}-install_only_stripped.tar.gz"
        assets = [a for a in release["assets"] if a["name"].startswith("cpython-") and a["name"].endswith(suffix)]
        if not assets:
            raise SystemExit(f"no python-build-standalone build for {target} in release {release.get('tag_name')}")

        def ver_of(a):          # cpython-3.12.12+20251014-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
            parts = a["name"].split("-")[1].split("+")[0].split(".")
            return tuple(int(x) for x in parts) if all(x.isdigit() for x in parts) else None

        assets = [a for a in assets if ver_of(a)]           # final releases only (no 3.15.0a1 ...)
        same = [a for a in assets if ver_of(a)[:2] == sys.version_info[:2]]
        url = max(same or assets, key=ver_of)["browser_download_url"]
    tar_path = cache / urllib.parse.unquote(url.rsplit("/", 1)[-1])
    if not tar_path.exists():
        download(url, tar_path)
    tmp = pydir.parent / "_python_extract"
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(tar_path) as t:
        if sys.version_info >= (3, 12):
            t.extractall(tmp, filter="data")
        else:
            t.extractall(tmp)
    shutil.move(str(tmp / "python"), str(pydir))     # the tarball holds one top folder: python/
    shutil.rmtree(tmp, ignore_errors=True)
    python = pydir / "bin" / "python3"
    ver = subprocess.run([python, "-c", "import platform; print(platform.python_version())"],
                         capture_output=True, text=True, check=True).stdout.strip()
    lib = next((pydir / "lib").glob("python3.*"))
    for extra in ("test", "idlelib", "turtledemo"):      # the stdlib's own test suite and IDLE: ~40 MB nobody ships
        shutil.rmtree(lib / extra, ignore_errors=True)
    return python, ver, lib / "site-packages"


def install_requirements(cache, ver, site):
    """pip of THIS Python downloads binary wheels for the BUNDLED Python (--python-version /
    --platform / --implementation) and unpacks them into its site-packages (--target).
    --only-binary: an embeddable / standalone Python has no compiler, so nothing may be built."""
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
           "--python-version", ver, "--implementation", "cp", "--target", site, "--cache-dir", cache / "pip"]
    for tag in wheel_platforms():
        cmd += ["--platform", tag]
    run(cmd + ["-r", ROOT / "backend" / "requirements.txt"])


def add_vc_runtime(pydir):
    """Windows only.  Compiled packages (numpy ...) are built with MSVC and need the Visual C++
    runtime; the embeddable zip brings vcruntime140*.dll but not msvcp140.dll.  Copy them from
    this PC so the target PC does not need the 'VC++ Redistributable' installed."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll", "vcomp140.dll", "concrt140.dll"):
        if not (pydir / name).exists() and (system32 / name).exists():
            shutil.copy2(system32 / name, pydir / name)


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--qt", help="Qt kit folder (default: newest under C:/Qt, ~/Qt or /opt/Qt)")
    ap.add_argument("--no-archive", "--no-zip", action="store_true", help="leave out the .zip / .tar.gz")
    ap.add_argument("--python-url", help="Linux/macOS: python-build-standalone *-install_only_stripped.tar.gz to use")
    args = ap.parse_args()

    qt = find_qt(args.qt)
    cmake, generator, cmake_args, env, compiler = toolchain(qt)
    print(f"OS       : {OS_NAME} {CPU}\nQt kit   : {qt or 'system'}\nCompiler : {compiler}\n"
          f"CMake    : {cmake} ({generator})\nPython   : {sys.executable} ({platform.python_version()})")

    build = ROOT / "build" / "package"
    cache = ROOT / "build" / "cache"
    dist = ROOT / "dist" / APP
    cache.mkdir(parents=True, exist_ok=True)
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    # ------------------------------------------------------------------ 1. front end
    step("1/6  build the Qt front end (Release)")
    build.mkdir(parents=True, exist_ok=True)
    configure = [cmake, "-S", ROOT, "-B", build, "-G", generator, "-DCMAKE_BUILD_TYPE=Release", *cmake_args]
    if qt:
        configure.append(f"-DCMAKE_PREFIX_PATH={qt}")
    run(configure, env=env)
    run([cmake, "--build", build, "--parallel"], env=env)

    # ------------------------------------------------------------------ 2. Qt runtime
    if WINDOWS:
        step("2/6  copy the exe, windeployqt adds the Qt runtime next to it")
        exe = dist / f"{APP}.exe"
        shutil.copy2(build / f"{APP}.exe", exe)
        # --qmldir: scan our QML to copy exactly the QML modules it imports
        # --compiler-runtime: the MinGW C++ runtime DLLs (libstdc++-6 ...)
        run([qt / "bin" / "windeployqt.exe", "--release", "--qmldir", ROOT / "frontend" / "qml",
             "--no-translations", "--compiler-runtime", exe], env=env, stdout=subprocess.DEVNULL)
        bundle_root = dist                      # backend/ goes next to the exe
    else:
        step("2/6  cmake --install: Qt's deployment script adds the Qt runtime (bin/ lib/ plugins/ qml/)")
        run([cmake, "--install", build, "--prefix", dist], env=env, stdout=subprocess.DEVNULL)
        if MACOS:
            exe = dist / f"{APP}.app" / "Contents" / "MacOS" / APP
            bundle_root = dist / f"{APP}.app" / "Contents" / "Resources"
        else:
            exe = dist / "bin" / APP
            bundle_root = dist                  # backend/ goes next to bin/
            qt_conf = dist / "bin" / "qt.conf"  # tells Qt where plugins/ and qml/ are; the
            if not qt_conf.exists():            # deploy script writes it, this is a fallback
                qt_conf.write_text("[Paths]\nPrefix=..\nPlugins=plugins\nQmlImports=qml\n", encoding="ascii")
            # The deploy script copies only the platform plugin a desktop needs (xcb).
            # Add the headless ones, so the bundle also runs in CI / over SSH
            # (QT_QPA_PLATFORM=offscreen), which the self-test below relies on.
            src = qt / "plugins" / "platforms" if qt else next(Path("/usr/lib").glob("*/qt6/plugins/platforms"), None)
            for name in ("libqoffscreen.so", "libqminimal.so"):
                if src and (src / name).exists() and not (dist / "plugins" / "platforms" / name).exists():
                    shutil.copy2(src / name, dist / "plugins" / "platforms" / name)
        if not exe.exists():
            raise SystemExit(f"{exe} missing after cmake --install")

    # ------------------------------------------------------------------ 3. Python code
    step("3/6  copy the Python code (backend/)")
    backend = bundle_root / "backend"
    shutil.copytree(ROOT / "backend", backend, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "python"))

    # ------------------------------------------------------------------ 4. private Python
    pydir = backend / "python"
    if WINDOWS:
        step("4/6  private Python: python.org embeddable zip + requirements.txt")
        python, ver, site = embeddable_python(cache, pydir)
    else:
        step("4/6  private Python: python-build-standalone + requirements.txt")
        python, ver, site = standalone_python(cache, pydir, args.python_url)
    print(f"  bundled Python {ver}: {python.relative_to(dist)}")
    install_requirements(cache, ver, site)
    if WINDOWS:
        add_vc_runtime(pydir)

    # ------------------------------------------------------------------ 5. tests
    step("5/6  test a: the bundled Python answers a request")
    req = b'{"id": 1, "method": "stats", "params": {"values": [1, 2, 3, 4]}}\n'
    out = subprocess.run([python, "-u", backend / "backend.py"], input=req, capture_output=True,
                         cwd=backend, timeout=120)
    print("  ", out.stdout.decode(errors="replace").strip().replace("\n", "\n   "))
    if out.returncode != 0 or b'"mean": 2.5' not in out.stdout:
        print(out.stderr.decode(errors="replace"))
        raise SystemExit("the bundled back end does not work")

    step("5/6  test b: every library the bundle needs is inside it (tools/check_deps.py)")
    sys.path.insert(0, str(ROOT / "tools"))
    import check_deps  # noqa: E402
    if check_deps.main(["check_deps.py", str(dist)]) != 0:
        raise SystemExit("bundle incomplete: see the missing libraries above")

    step("5/6  test c: the packaged app itself, --selftest, with nothing of this machine on PATH")
    clean = {k: v for k, v in os.environ.items()
             if k not in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH",
                          "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")}
    if WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        clean["PATH"] = f"{root}\\System32;{root}"
    else:
        clean["PATH"] = "/usr/bin:/bin"
        if LINUX and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            clean["QT_QPA_PLATFORM"] = "offscreen"      # no desktop here (CI, WSL): headless Qt
    print("  $", exe.relative_to(dist), "--selftest", flush=True)
    t = subprocess.run([exe, "--selftest"], env=clean, capture_output=True, text=True, timeout=120)
    if t.returncode == 0:
        print("   exit 0: the packaged front end started the bundled Python, called add(2, 3) and got 5")
    else:
        print(t.stderr.strip())
        if "platform plugin" in t.stderr.lower():
            print("   WARNING: Qt could not open a display here; run the self-test on a desktop machine")
        else:
            raise SystemExit(f"self-test failed with exit code {t.returncode} (1 = back end error, 2 = timeout)")

    # ------------------------------------------------------------------ 6. archive
    step("6/6  README.txt + archive")
    launch = f"{APP}.exe" if WINDOWS else f"bin/{APP}" if LINUX else f"{APP}.app"
    pyrel = ("backend\\python\\python.exe backend\\backend.py" if WINDOWS
             else "backend/python/bin/python3 backend/backend.py")
    notes = ("Keep the folder together; unzip to a local disk (not a network share).\n" if WINDOWS else
             "Keep the folder together.  Needs the usual desktop libraries of a Linux distribution\n"
             "(X11/xcb incl. libxcb-cursor0, fontconfig, OpenGL/Mesa, glib) - present on every desktop\n"
             "install, missing only in minimal containers.\n")
    (dist / "README.txt").write_text(
        f"{APP} - Qt front end + Python back end, standalone\n\n"
        f"Start {launch}.  Nothing needs to be installed: the Qt runtime is in this folder and\n"
        "backend/python/ is a complete private Python with its packages.\n" + notes +
        f"If the status says 'error', run   {pyrel}   in a terminal inside this folder and type a\n"
        'request such as   {"id": 1, "method": "add", "params": {"a": 2, "b": 3}}   to see the Python error.\n',
        encoding="utf-8")

    size = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"\nbundle : {dist}  ({size / 1e6:.0f} MB, {sum(1 for _ in dist.rglob('*'))} files)")
    if not args.no_archive:
        if WINDOWS:
            archive = dist.parent / f"{APP}-{OS_NAME}-{CPU}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(dist.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(dist.parent).as_posix())
        else:   # tar keeps the executable bits and the symlinks of the Qt libraries
            archive = dist.parent / f"{APP}-{OS_NAME}-{CPU}.tar.gz"
            with tarfile.open(archive, "w:gz") as t:
                t.add(dist, arcname=APP)
        print(f"archive: {archive}  ({archive.stat().st_size / 1e6:.0f} MB)")
    print(f"\nCopy the folder (or the archive) to another machine and start {launch}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

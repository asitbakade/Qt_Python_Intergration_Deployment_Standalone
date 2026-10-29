"""Release check of the bundle: every shared library that any program file in
it needs is either inside the bundle or part of the operating system.

    python tools/check_deps.py dist/QtPythonDemo        (any OS; used by package.py)

Windows: reads the import table of each .exe / .dll / .pyd (a small PE reader,
no third-party module) and fails when something would only load thanks to a
library installed on the build machine - e.g. torch's MSVCP140.dll /
VCRUNTIME140_THREADS.dll, which come from the Visual C++ Redistributable and
are not on a fresh Windows.  Windows' own DLLs (kernel32, api-ms-win-*, ...)
are allowed; anything else must travel with the package.

Linux: runs `ldd` on every ELF file (executables, .so) and fails on any
"not found".  Libraries resolved from the system (libc, libGL, libxcb-*, ...)
are listed for information: they must exist on the target distribution too.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

# Windows system libraries present on every supported Windows (10 / 11, Server
# 2016+): a package may import these without shipping them.
WINDOWS_DLLS = {
    "advapi32", "authz", "avrt", "bcrypt", "bluetoothapis", "cabinet", "cfgmgr32", "comctl32", "comdlg32",
    "credui", "crypt32", "cryptui", "d2d1", "d3d11", "d3d12", "d3d9", "d3dcompiler_47", "dbghelp", "dcomp",
    "dhcpcsvc", "dhcpcsvc6", "dnsapi", "dsound", "dwmapi", "dwrite", "dxgi", "dxva2", "evr", "gdi32",
    "gdiplus", "glu32", "hid", "imagehlp", "imm32", "iphlpapi", "kernel32", "ksuser", "mf", "mfplat",
    "mfreadwrite", "mfuuid", "mmdevapi", "mpr", "msimg32", "msvcrt", "mswsock", "ncrypt", "netapi32",
    "normaliz", "ntdll", "odbc32", "ole32", "oleacc", "oleaut32", "opengl32", "pdh", "powrprof", "propsys",
    "psapi", "rpcrt4", "secur32", "setupapi", "shcore", "shell32", "shlwapi", "strmiids", "synchronization",
    "ucrtbase", "user32", "userenv", "uxtheme", "version", "winhttp", "wininet", "winmm", "winspool",
    "wintrust", "wlanapi", "wldap32", "ws2_32", "wsock32", "wtsapi32", "xinput1_4", "xinput9_1_0",
    "windows.storage", "windowscodecs", "windows.ui", "windows.data.pdf", "d3d10warp", "dxcore",
    "api-ms-win-shcore-scaling-l1-1-1", "runtimeobject", "cldapi", "sspicli", "coremessaging",
    "inputhost", "twinapi.appcore", "dcomp", "wer", "winsta", "wevtapi", "tbs", "mscoree",
}


def imported_dlls(path: Path) -> list[str]:
    """Names in the PE import directory (lower case, without a trailing .dll); [] for a file that is not a PE32+."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        return []
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return []
    coff = pe + 4
    n_sections, opt_size = struct.unpack_from("<H", data, coff + 2)[0], struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)      # data directories (PE32+ / PE32)
    import_rva, import_size = struct.unpack_from("<II", data, dd + 8)   # entry 1 = import table
    if import_rva == 0:
        return []
    sections = []
    off = opt + opt_size
    for _ in range(n_sections):
        vsize, vaddr, rsize, raw = struct.unpack_from("<IIII", data, off + 8)
        sections.append((vaddr, max(vsize, rsize), raw))
        off += 40

    def rva2off(rva: int) -> int:
        for vaddr, size, raw in sections:
            if vaddr <= rva < vaddr + size:
                return raw + rva - vaddr
        raise ValueError(f"rva {rva:#x} outside the sections of {path.name}")

    names = []
    off = rva2off(import_rva)
    while True:
        lookup, _, _, name_rva, _ = struct.unpack_from("<IIIII", data, off)
        if name_rva == 0:
            break
        n = rva2off(name_rva)
        names.append(data[n:data.index(b"\0", n)].decode("ascii", "replace").lower().removesuffix(".dll"))
        off += 20
    return names


# Imported by an optional code path and absent on purpose: numba's TBB
# threading layer (it falls back to its own work queue).
OPTIONAL_DLLS = {"tbb12"}


def allowed(name: str) -> bool:
    return (name in WINDOWS_DLLS or name in OPTIONAL_DLLS
            or name.startswith(("api-ms-win-", "ext-ms-", "windows.")))


def search_dirs(p: Path, dist: Path) -> list[Path]:
    """Where Windows looks for a DLL that `p` imports: p's own folder, the
    folder of the executable that loads it (the python folder for everything under
    it, the package root for the Qt app), and the folders a Python
    package adds with os.add_dll_directory on import - its own tree
    (torch/lib) and its delvewheel folder (<pkg>.libs, faiss_cpu.libs)."""
    dirs = [p.parent, dist]
    # the private Python: the nearest folder above p that holds python.exe
    pydir = next((a for a in p.parents if a != dist and (a / "python.exe").exists()), None)
    if pydir:
        dirs.append(pydir)
        site = pydir / "Lib" / "site-packages"
        if site in p.parents:
            rel = p.relative_to(site)
            if len(rel.parts) > 1:
                top = rel.parts[0]
                stem = top.split("_")[0].removesuffix(".libs")
                dirs += [d for d in site.iterdir() if d.is_dir() and d.name.lower().startswith(stem.lower())]
                dirs += [d for d in (site / top).rglob("*") if d.is_dir()] if (site / top).is_dir() else []
    return dirs


def resolves(name: str, p: Path, dist: Path) -> bool:
    for d in search_dirs(p, dist):
        if (d / f"{name}.dll").exists() or (d / f"{name}.DLL").exists() or (d / f"{name}.pyd").exists():
            return True
    return False


# ----------------------------------------------------------------------- Linux

def is_elf(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"ELF"
    except OSError:
        return False


def check_linux(dist: Path) -> int:
    files = [p for p in dist.rglob("*") if p.is_file() and not p.is_symlink()
             and (".so" in p.suffixes or p.suffix == "" or p.suffix.startswith(".so")) and is_elf(p)]
    missing: dict[str, set[str]] = {}
    system: dict[str, set[str]] = {}
    for p in files:
        out = subprocess.run(["ldd", str(p)], capture_output=True, text=True, env={**os.environ, "LANG": "C"})
        for line in out.stdout.splitlines():
            if "=>" not in line:
                continue                                  # the vdso and the ELF loader itself
            name, _, where = line.strip().partition("=>")
            name, where = name.strip(), where.strip()
            rel = str(p.relative_to(dist))
            if where.startswith("not found"):
                missing.setdefault(name, set()).add(rel)
            elif not where.split(" ")[0].startswith(str(dist)):
                system.setdefault(name, set()).add(rel)
    print(f"{len(files)} ELF files in {dist}")
    if system:
        names = sorted(system)
        print(f"{len(names)} libraries come from the operating system (the target distribution must have them):")
        print("  " + ", ".join(names))
    if not missing:
        print("OK: every other library is found inside the bundle (rpath / plugin folders)")
        return 0
    print(f"FAIL: {len(missing)} libraries are not found anywhere:")
    for n in sorted(missing):
        users = sorted(missing[n])
        print(f"  {n}  <- {', '.join(users[:3])}{' ...' if len(users) > 3 else ''} ({len(users)} files)")
    return 1


# --------------------------------------------------------------------- Windows

def check_windows(dist: Path) -> int:
    files = [p for p in dist.rglob("*") if p.suffix.lower() in (".exe", ".dll", ".pyd")]
    missing: dict[str, set[str]] = {}
    for p in files:
        try:
            names = imported_dlls(p)
        except (ValueError, struct.error, IndexError) as exc:
            print(f"  ! cannot read {p.relative_to(dist)}: {exc}")
            continue
        for n in names:
            if allowed(n) or n.startswith("python3") or resolves(n, p, dist):
                continue
            missing.setdefault(n, set()).add(str(p.relative_to(dist)))
    print(f"{len(files)} program files in {dist}")
    if not missing:
        print("OK: every imported DLL is found next to its importer, in the package root / python folder, or is part of Windows")
        return 0
    print(f"FAIL: {len(missing)} imported DLLs would not be found on a fresh Windows:")
    for n in sorted(missing):
        users = sorted(missing[n])
        print(f"  {n}.dll  <- {', '.join(users[:3])}{' ...' if len(users) > 3 else ''} ({len(users)} files)")
    return 1


def main(argv: list[str]) -> int:
    dist = Path(argv[1] if len(argv) > 1 else "dist/QtPythonDemo").resolve()
    if sys.platform == "win32":
        return check_windows(dist)
    if sys.platform == "darwin":
        print("macOS: no dependency check implemented (use `otool -L` on the .app); skipping")
        return 0
    return check_linux(dist)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

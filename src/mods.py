"""Mod manager: list, install from RAR/ZIP/7z, enable/disable, remove. No GUI code here (testable).

Folder rules (must match d3dx.ini: include_recursive = Mods, exclude_recursive = DISABLED*):
  3dmigoto/Mods/<Name>            enabled mod
  3dmigoto/Mods/DISABLED_<Name>   disabled mod (3DMigoto ignores it)
  3dmigoto/Mods_staging/          temporary extraction area (outside Mods on purpose)
  3dmigoto/Mods_trash/            removed / replaced mods (recoverable by hand)
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

DISABLED_PREFIX = "DISABLED_"
SYSTEM_FOLDERS = ("buffervalues",)  # holds ORFix.ini; hidden from the mod list so it cannot be removed by accident
ARCHIVE_EXTS = (".rar", ".zip", ".7z")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ModError(Exception):
    """Problem with a message that is safe to show to the user."""


# ----------------------------------------------------------------------------- paths
def mods_dir(base): return Path(base) / "3dmigoto" / "Mods"
def staging_dir(base): return Path(base) / "3dmigoto" / "Mods_staging"
def trash_dir(base): return Path(base) / "3dmigoto" / "Mods_trash"


def _stamp(): return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "Mod"


def is_disabled(folder_name): return folder_name.lower().startswith(DISABLED_PREFIX.lower())


def plain_name(folder_name):
    return folder_name[len(DISABLED_PREFIX):] if is_disabled(folder_name) else folder_name


# ----------------------------------------------------------------------------- extractor
def find_extractor(base):
    """Returns (kind, path) or None. kind: '7z' | 'unrar' | 'tar'. Bundled 7-Zip is preferred."""
    base = Path(base)
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    seven = [base / "tools" / "7z.exe", base / "7z.exe"] + [Path(p) / "7-Zip" / "7z.exe" for p in pf]
    seven += [Path(p) for p in filter(None, (shutil.which(n) for n in ("7z", "7zz", "7za")))]
    for c in seven:
        if c.is_file():
            return "7z", c
    for p in pf:
        c = Path(p) / "WinRAR" / "UnRAR.exe"
        if c.is_file():
            return "unrar", c
    t = shutil.which("unrar")
    if t:
        return "unrar", Path(t)
    t = shutil.which("tar")  # Windows 10/11 bsdtar can read most RAR files
    if t:
        return "tar", Path(t)
    return None


def _safe_unzip(archive, dest):
    dest = Path(dest).resolve()
    with zipfile.ZipFile(archive) as z:
        for m in z.infolist():
            if not (dest / m.filename).resolve().is_relative_to(dest):
                raise ModError("Archive contains an unsafe path (%s). Not extracted." % m.filename)
        z.extractall(dest)


def extract_archive(archive, dest, base, log=None):
    archive, dest = Path(archive), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ext = archive.suffix.lower()
    if ext == ".zip":
        try:
            return _safe_unzip(archive, dest)
        except zipfile.BadZipFile:
            raise ModError("%s is not a valid ZIP file (damaged download?)." % archive.name)
    ex = find_extractor(base)
    if not ex:
        raise ModError("No tool to open %s files found. Install 7-Zip (free, 7-zip.org) and try again." % ext)
    kind, exe = ex
    if kind == "7z":
        cmd = [str(exe), "x", "-y", "-aoa", "-o" + str(dest), str(archive)]
    elif kind == "unrar":
        cmd = [str(exe), "x", "-y", "-o+", str(archive), str(dest) + os.sep]
    else:
        cmd = [str(exe), "-xf", str(archive), "-C", str(dest)]
    if log:
        log.info("Extracting with %s: %s" % (kind, archive.name))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ModError("Extracting %s took too long and was stopped." % archive.name)
    except OSError as e:
        raise ModError("Could not run the extractor (%s)." % e)
    if r.returncode not in (0, 1) or not any(dest.iterdir()):
        if log:
            log.error("extractor output:\n%s" % (r.stdout + r.stderr)[-2000:])
        raise ModError("Could not extract %s (damaged file, password-protected, or unsupported). Details are in the log." % archive.name)


# ----------------------------------------------------------------------------- install
def find_mod_root(staging, fallback_name):
    """Where the real mod starts inside an extracted archive, and its name."""
    root, name = Path(staging), clean_name(fallback_name)
    if not any(root.rglob("*.ini")):
        raise ModError("This archive has no .ini file, so it is not a 3DMigoto mod.")
    while True:
        entries = [e for e in root.iterdir()]
        dirs = [e for e in entries if e.is_dir()]
        if len(dirs) == 1 and not any(e.suffix.lower() == ".ini" for e in entries if e.is_file()):
            root = dirs[0]
            name = clean_name(root.name)
            continue
        return root, name


def install_mod(archive, base, log=None):
    """Extract + normalise + move into Mods. Returns installed folder name. Replaces same-name mod (old one -> trash)."""
    archive = Path(archive)
    if archive.suffix.lower() not in ARCHIVE_EXTS:
        raise ModError("%s is not a .rar / .zip / .7z file." % archive.name)
    stage = staging_dir(base) / ("%s_%s" % (_stamp(), os.getpid()))
    try:
        extract_archive(archive, stage, base, log)
        root, name = find_mod_root(stage, archive.stem)
        dest_dir = mods_dir(base)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for existing in (name, DISABLED_PREFIX + name):
            old = dest_dir / existing
            if old.exists():
                t = trash_dir(base) / ("%s_%s" % (existing, _stamp()))
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(t))
                if log:
                    log.warn("A mod named '%s' already existed - the old copy was moved to Mods_trash." % existing)
        shutil.move(str(root), str(dest_dir / name))
        if log:
            log.info("Installed mod: %s" % name)
        return name
    finally:
        shutil.rmtree(staging_dir(base), ignore_errors=True)


# ----------------------------------------------------------------------------- list / toggle / remove
def _size(folder):
    total = 0
    for f in folder.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def list_mods(base, by_mod=None):
    """One entry per mod folder: name, folder, enabled, ini_count, size, warnings."""
    by_mod = by_mod or {}
    out = []
    d = mods_dir(base)
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir(), key=lambda p: plain_name(p.name).lower()):
        if not f.is_dir() or f.name.startswith(".") or f.name.lower() in SYSTEM_FOLDERS:
            continue
        out.append({"name": plain_name(f.name), "folder": f.name, "enabled": not is_disabled(f.name),
                    "ini_count": sum(1 for _ in f.rglob("*.ini")), "size": _size(f),
                    "warnings": [] if is_disabled(f.name) else list(by_mod.get(f.name, []))})
    return out


def set_enabled(base, folder, enabled):
    d = mods_dir(base)
    src = d / folder
    if not src.is_dir():
        raise ModError("Mod folder not found: %s" % folder)
    new = plain_name(folder) if enabled else DISABLED_PREFIX + plain_name(folder)
    if new == folder:
        return folder
    if (d / new).exists():
        raise ModError("Cannot switch: a folder named '%s' already exists." % new)
    src.rename(d / new)
    return new


def remove_mod(base, folder):
    src = mods_dir(base) / folder
    if not src.is_dir():
        raise ModError("Mod folder not found: %s" % folder)
    dest = trash_dir(base) / ("%s_%s" % (folder, _stamp()))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


# ----------------------------------------------------------------------------- update 3DMigoto itself
CORE_KEEP = {"mods", "mods_trash", "mods_staging", "shadercache", "shaderfromgame"}


def find_core_root(stage):
    hits = [p.parent for p in Path(stage).rglob("d3d11.dll") if (p.parent / "d3dx.ini").exists()]
    if not hits:
        raise ModError("This archive is not a 3DMigoto package (no d3d11.dll + d3dx.ini inside).")
    return sorted(hits, key=lambda p: len(p.parts))[0]


def install_core(archive, base, log=None):
    """Replace 3DMigoto files (dll, loader, d3dx.ini, ShaderFixes, Core...) from a package; keep Mods. Returns backup folder."""
    archive = Path(archive)
    if archive.suffix.lower() not in ARCHIVE_EXTS:
        raise ModError("%s is not a .rar / .zip / .7z file." % archive.name)
    mig = Path(base) / "3dmigoto"
    stage = staging_dir(base) / ("core_%s" % _stamp())
    try:
        extract_archive(archive, stage, base, log)
        root = find_core_root(stage)
        bak = Path(base) / "backups" / ("3dmigoto_before_update_%s" % _stamp())
        bak.mkdir(parents=True)
        for item in mig.iterdir():
            if item.name.lower() in CORE_KEEP:
                continue
            (shutil.copytree if item.is_dir() else shutil.copy2)(str(item), str(bak / item.name))
        for item in root.iterdir():
            if item.name.lower() == "mods":  # never overwrite the user's mods; only add missing files
                for f in item.rglob("*"):
                    tgt = mig / "Mods" / f.relative_to(item)
                    if f.is_file() and not tgt.exists():
                        tgt.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(f), str(tgt))
            elif item.is_dir():
                shutil.copytree(str(item), str(mig / item.name), dirs_exist_ok=True)
            else:
                shutil.copy2(str(item), str(mig / item.name))
        (mig / "d3dx.ini.orig").unlink(missing_ok=True)
        if log:
            log.info("3DMigoto updated from %s. Old files saved in %s" % (archive.name, bak))
        return bak
    finally:
        shutil.rmtree(staging_dir(base), ignore_errors=True)


# ----------------------------------------------------------------------------- fix tools (GIFix / ORFix Applier)
FIX_TOOLS = (("GIFix  (update older mods to the current version)", "gifix*.exe"),
             ("ORFix Applier  (add the ORFix reflection/outline fix to mods)", "orfixapplier*.exe"))


def orfix_installed(base):
    d = mods_dir(base)
    return d.is_dir() and any(p.name.lower() == "orfix.ini" for p in d.rglob("*.ini"))


def find_fix_tools(base):
    """[(label, exe_path)] for tools found in the Mods folder (newest file name wins)."""
    d, out = mods_dir(base), []
    for label, pattern in FIX_TOOLS:
        hits = sorted(d.glob(pattern), key=lambda p: p.name.lower()) if d.is_dir() else []
        if hits:
            out.append((label, hits[-1]))
    return out


def run_fix_tool(base, exe, log=None):
    """Backs up every mod .ini (the files these tools edit), then opens the tool in its own console in Mods."""
    d = mods_dir(base)
    exe = Path(exe)
    if not exe.is_file():
        raise ModError("Tool not found: %s" % exe.name)
    bak = Path(base) / "backups" / ("mods_ini_before_fix_%s" % _stamp())
    n = 0
    for f in d.rglob("*.ini"):
        tgt = bak / f.relative_to(d)
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(f), str(tgt))
        n += 1
    if log:
        log.info("Backed up %d .ini file(s) to %s" % (n, bak))
        log.info("Starting %s in its own window (cwd = Mods). Answer its questions there, then click Rescan." % exe.name)
    flags = 0x00000010 if os.name == "nt" else 0  # CREATE_NEW_CONSOLE
    subprocess.Popen([str(exe)], cwd=str(d), creationflags=flags)
    return bak

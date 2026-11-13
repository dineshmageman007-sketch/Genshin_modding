"""GenshinModLauncher - one-click start for Genshin Impact + 3DMigoto mods.

What it does (every step is written to logs/launcher_<time>.log):
  1. Asks for admin rights once (the game and the loader need them).
  2. Checks that 3DMigoto files are all there.
  3. Finds GenshinImpact.exe by itself (config -> d3dx.ini -> registry -> common folders).
  4. Scans Mods/ for the usual "skin does not load" causes (missing files, two skins fighting
     over the same hash).
  5. Writes target/launch into d3dx.ini, starts the 3DMigoto Loader, which starts the game.
  6. Verifies that 3DMigoto really loaded inside the game, archives its log, and writes
     logs/bug_report_latest.zip (send this file when something is wrong).

Run:  GenshinModLauncher.exe            (normal)
      GenshinModLauncher.exe --check    (only check + scan mods, do not start the game)
      GenshinModLauncher.exe --debug    (extra 3DMigoto logging for one run)
Dev:  python src/launcher.py --check
"""
import argparse
import csv
import ctypes
import datetime as dt
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

from mods import SYSTEM_FOLDERS

VERSION = "1.0.0"
GAME_EXES = ("GenshinImpact.exe", "YuanShen.exe")
LOADER_EXE = "3DMigoto Loader.exe"
LOADER_PROC = "3dmigoto loader.exe"
REQUIRED_FILES = (LOADER_EXE, "d3d11.dll", "d3dx.ini")

EXIT_OK, EXIT_ERROR, EXIT_PREFLIGHT, EXIT_NO_GAME, EXIT_GAME_RUNNING, EXIT_LOADER, EXIT_NO_START = range(7)

DEFAULT_CONFIG = {
    "game_exe": "",                       # full path to GenshinImpact.exe; "" = find automatically
    "debug_logging": False,               # true = unbuffered 3DMigoto log + crash dump (slower)
    "stay_open_until_game_exits": True,   # keeps window open to archive the log when the game closes
    "wait_for_game_seconds": 90,          # how long to wait for the game process / 3DMigoto log
    "loader_timeout_seconds": 120,        # how long to wait for the loader itself
    "keep_logs": 20,                      # old launcher/3DMigoto logs to keep
    "skip_mod_check": False,
    "pause_on_error": True,
}

_sleep = time.sleep  # tests replace this


# ----------------------------------------------------------------------------- logging
class Log:
    """Writes to the console and to a file. Never crashes on odd characters in mod names."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a", encoding="utf-8", errors="replace")
        self.listeners = []  # GUI hooks: callable(level, line)

    def _w(self, level, msg):
        line = "%s [%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), level, msg)
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))
        self.f.write(line + "\n")
        self.f.flush()
        for cb in self.listeners:
            try:
                cb(level, line)
            except Exception:
                pass

    def info(self, m): self._w("INFO ", m)
    def warn(self, m): self._w("WARN ", m)
    def error(self, m): self._w("ERROR", m)
    def step(self, m): self._w("STEP ", "---- " + m)


# ----------------------------------------------------------------------------- basics
def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_admin():
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline([str(Path(sys.argv[0]).resolve())] + sys.argv[1:])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32


def load_config(path, log):
    cfg = dict(DEFAULT_CONFIG)
    path = Path(path)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
        except Exception as e:
            log.warn("launcher_config.json is broken (%s) - using defaults. Fix or delete it." % e)
    else:
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        log.info("Created launcher_config.json with defaults.")
    return cfg


def running_processes():
    """Lower-case names of running processes."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 errors="replace", creationflags=0x08000000).stdout
            return [r[0].lower() for r in csv.reader(io.StringIO(out)) if r]
        out = subprocess.run(["ps", "-e", "-o", "comm="], capture_output=True, text=True).stdout
        return [l.strip().lower() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def game_is_running():
    procs = running_processes()
    return any(g.lower() in procs for g in GAME_EXES)


# ----------------------------------------------------------------------------- d3dx.ini helpers
def read_raw(path):
    return Path(path).read_bytes().decode("utf-8", errors="surrogateescape")


def write_raw(path, text):
    Path(path).write_bytes(text.encode("utf-8", errors="surrogateescape"))


def get_ini_value(text, section, key):
    cur = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\[(.+?)\]", s)
        if m:
            cur = m.group(1).lower()
            continue
        if cur == section.lower() and not s.startswith(";"):
            m = re.match(r"^%s\s*=\s*(.*?)\s*(?:;.*)?$" % re.escape(key), s, re.I)
            if m:
                return m.group(1)
    return None


def patch_ini(text, updates):
    """updates = {(section, key): value}. Changes only those keys, keeps everything else as is."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for (section, key), value in updates.items():
        start = None
        for i, l in enumerate(lines):
            m = re.match(r"^\s*\[(.+?)\]", l)
            if m and m.group(1).lower() == section.lower():
                start = i
                break
        new = "%s = %s" % (key, value)
        if start is None:
            lines += ["", "[%s]" % section, new]
            continue
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^\s*\[.+?\]", lines[j]):
                end = j
                break
        for j in range(start + 1, end):
            if re.match(r"^\s*%s\s*=" % re.escape(key), lines[j], re.I):
                lines[j] = new
                break
        else:
            lines.insert(start + 1, new)
    return nl.join(lines)


# ----------------------------------------------------------------------------- finding the game
def registry_install_dirs():
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for key in (r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Genshin Impact",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Genshin Impact"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                p = winreg.QueryValueEx(k, "InstallPath")[0]
                out += [Path(p) / "Genshin Impact Game", Path(p)]
        except OSError:
            pass
    return out


def default_roots():
    if os.name != "nt":
        return []
    return [Path("%s:\\" % d) for d in "CDEFGH" if Path("%s:\\" % d).exists()]


COMMON_SUBDIRS = (
    r"Program Files\Genshin Impact\Genshin Impact Game",
    r"Program Files\HoYoPlay\games\Genshin Impact game",
    r"Program Files (x86)\Genshin Impact\Genshin Impact Game",
    r"Games\Genshin Impact\Genshin Impact Game",
    r"Genshin Impact\Genshin Impact Game",
    r"HoYoPlay\games\Genshin Impact game",
)


def find_game(cfg, ini_target, log, roots=None, reg_dirs=None):
    cands = []
    if cfg.get("game_exe"):
        cands.append(Path(cfg["game_exe"]))
    if ini_target:
        cands.append(Path(ini_target))
    for d in (registry_install_dirs() if reg_dirs is None else reg_dirs):
        cands += [Path(d) / g for g in GAME_EXES]
    for r in (default_roots() if roots is None else roots):
        for sub in COMMON_SUBDIRS:
            cands += [Path(r).joinpath(*sub.split("\\")) / g for g in GAME_EXES]
    for c in cands:
        try:
            if c.is_file():
                log.info("Game found: %s" % c)
                return c
        except OSError:
            pass
    return None


# ----------------------------------------------------------------------------- mod scan
SECTION_RE = re.compile(r"^\s*\[(.+?)\]")
KV_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*(.*?)\s*(?:;.*)?$")


NEEDS_KEYS = ("override_vertex_count", "override_byte_stride", "shaderfixes\\help.ini", "commandlist\\global\\orfix")


def core_problems(mig, scan):
    """Does the installed 3DMigoto know what the enabled mods ask for? Returns list of messages."""
    needs = (scan or {}).get("needs", {})
    mig = Path(mig)
    try:
        blob = (mig / "d3d11.dll").read_bytes()
    except OSError:
        return []
    out, lacking, mods = [], [], []
    for key in NEEDS_KEYS[:2]:
        if key in needs and key.encode() not in blob and key.encode("utf-16le") not in blob:
            lacking.append(key)
            mods += [m for m in needs[key] if m not in mods]
    if lacking:
        out.append("OUTDATED 3DMIGOTO: %s need(s) %s, which your d3d11.dll does not support. "
                   "Click 'Update 3DMigoto' and choose the latest GIMI package." % (", ".join(mods), ", ".join(lacking)))
    hk = NEEDS_KEYS[2]
    if hk in needs and not (mig / "ShaderFixes" / "help.ini").exists():
        out.append("OUTDATED 3DMIGOTO: %s expect(s) ShaderFixes\\help.ini, which is missing. Click 'Update 3DMigoto'." % ", ".join(needs[hk]))
    ok = NEEDS_KEYS[3]
    if ok in needs and not any(p.name.lower() == "orfix.ini" for p in (mig / "Mods").rglob("*.ini")):
        out.append("MISSING ORFIX: %s call(s) ORFix, but Mods\\BufferValues\\ORFix.ini is not there. Put ORFix.ini in Mods\\BufferValues."
                   % ", ".join(needs[ok]))
    return out


def scan_mods(mods_dir, log=None):
    """Static check of every enabled mod .ini. Returns {'mods': [...], 'warnings': [...]}."""
    mods_dir = Path(mods_dir)
    result = {"mods": [], "warnings": [], "by_mod": {}, "needs": {}}

    def warn(mod, msg):
        result["warnings"].append(msg)
        result["by_mod"].setdefault(mod, []).append(msg)
    if not mods_dir.is_dir():
        return result
    hashes = {}
    mods = set()
    for ini in sorted(mods_dir.rglob("*.ini")):
        rel = ini.relative_to(mods_dir)
        if any(p.lower().startswith("disabled") for p in rel.parts):
            continue
        if len(rel.parts) > 1 and rel.parts[0].lower() in SYSTEM_FOLDERS:
            continue  # support files (ORFix.ini): not a skin, would give false conflicts
        mod = rel.parts[0] if len(rel.parts) > 1 else ini.stem
        mods.add(mod)
        try:
            text = ini.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError as e:
            warn(mod, "%s: cannot read (%s)" % (rel, e))
            continue
        low = text.lower()
        for feat in NEEDS_KEYS:
            if feat in low and mod not in result["needs"].setdefault(feat, []):
                result["needs"][feat].append(mod)
        section = ""
        for ln, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            m = SECTION_RE.match(s)
            if m:
                section = m.group(1)
                continue
            m = KV_RE.match(s)
            if not m:
                continue
            key, val = m.group(1).lower(), m.group(2)
            sl = section.lower()
            if key == "filename" and sl.startswith("resource") and val:
                target = Path(val) if re.match(r"^[A-Za-z]:[\\/]", val) else ini.parent / val.replace("\\", os.sep)
                if not target.exists():
                    warn(mod, "MISSING FILE in %s [%s] line %d: %s" % (rel, section, ln, val))
            elif key == "hash" and sl.startswith("textureoverride") and val:
                hashes.setdefault(val.lower(), set()).add((mod, section))
    for h, users in sorted(hashes.items()):
        names = sorted({u[0] for u in users})
        if len(names) > 1:
            for n in names:
                warn(n, "CONFLICT: shares hash %s with %s - turn one of them off"
                     % (h, ", ".join(x for x in names if x != n)))
    result["mods"] = sorted(mods)
    if log:
        log.info("Mods found: %d (%s)" % (len(mods), ", ".join(sorted(mods)) or "none"))
        for w in result["warnings"]:
            log.warn(w)
        if not result["warnings"] and mods:
            log.info("Mod scan: no problems found.")
    return result


# ----------------------------------------------------------------------------- 3DMigoto log
LOG_FLAGS = re.compile(r"(error|warning|fail|unrecogni[sz]ed|not found|missing|invalid|unable|could not|couldn't)", re.I)


def summarize_3dmigoto_log(path, limit=150):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if LOG_FLAGS.search(line):
            out.append(line.strip())
            if len(out) >= limit:
                out.append("... (more lines in the full log)")
                break
    return out


def archive_log(src, logs_dir, tag, move=False):
    src = Path(src)
    if not src.exists() or src.stat().st_size == 0:
        return None
    dest = Path(logs_dir) / ("3dmigoto_%s_%s.txt" % (tag, dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
    (shutil.move if move else shutil.copy2)(str(src), str(dest))
    return dest


def rotate_logs(logs_dir, keep):
    for pattern in ("launcher_*.log", "3dmigoto_*.txt"):
        files = sorted(Path(logs_dir).glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[max(keep, 1):]:
            try:
                old.unlink()
            except OSError:
                pass


def make_bug_report(base, log, scan, archived_log, summary):
    out = base / "logs" / "bug_report_latest.zip"
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(log.path, "launcher.log")
            if archived_log and Path(archived_log).exists():
                z.write(archived_log, "3dmigoto_log.txt")
            ini = base / "3dmigoto" / "d3dx.ini"
            if ini.exists():
                z.write(ini, "d3dx.ini")
            cfg = base / "launcher_config.json"
            if cfg.exists():
                z.write(cfg, "launcher_config.json")
            z.writestr("mod_scan.json", json.dumps(scan or {}, indent=2))
            z.writestr("3dmigoto_log_problems.txt", "\n".join(summary or ["(none or no log)"]))
            z.writestr("system.txt", "launcher %s\n%s\npython %s\nadmin=%s\n"
                       % (VERSION, platform.platform(), platform.python_version(), is_admin()))
        log.info("Bug report written: %s  (send this file if something is wrong)" % out)
    except Exception as e:
        log.warn("Could not write bug report: %s" % e)


# ----------------------------------------------------------------------------- loader
def start_loader(mig, timeout, log):
    """Runs the 3DMigoto Loader (it starts the game itself via launch=). Returns (rc, output)."""
    flags = 0x08000000 if os.name == "nt" else 0  # no extra console window
    p = subprocess.Popen([str(mig / LOADER_EXE)], cwd=str(mig), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=flags)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        log.warn("Loader did not finish within %ss and was stopped." % timeout)
        return None, out or ""


def wait_for(predicate, seconds):
    end = time.time() + seconds
    while True:
        if predicate():
            return True
        if time.time() >= end:
            return False
        _sleep(1)


# ----------------------------------------------------------------------------- main flow
def run(args, base, log):
    log.info("GenshinModLauncher %s | %s | admin=%s" % (VERSION, platform.platform(), is_admin()))
    cfg = load_config(base / "launcher_config.json", log)
    mig = base / "3dmigoto"
    logs_dir = base / "logs"
    mig_log = mig / "d3d11_log.txt"
    scan = None
    archived = None
    summary = []

    log.step("1/6 Check 3DMigoto files")
    missing = [f for f in REQUIRED_FILES if not (mig / f).is_file()]
    if missing:
        log.error("Missing in %s: %s. Re-download the project; do not move the exe away from the 3dmigoto folder." % (mig, ", ".join(missing)))
        return EXIT_PREFLIGHT
    (mig / "Mods").mkdir(exist_ok=True)
    log.info("OK")

    log.step("2/6 Find the game")
    ini_path = mig / "d3dx.ini"
    ini_text = read_raw(ini_path)
    game = find_game(cfg, get_ini_value(ini_text, "Loader", "target"), log)
    if not game:
        log.error("GenshinImpact.exe not found. Put its full path into launcher_config.json -> \"game_exe\".")
        return EXIT_NO_GAME

    log.step("3/6 Scan mods")
    if cfg["skip_mod_check"]:
        log.info("Skipped (skip_mod_check=true)")
    else:
        scan = scan_mods(mig / "Mods", log)
        scan["core_problems"] = core_problems(mig, scan)
        for p in scan["core_problems"]:
            log.error(p)
    if args.check:
        log.info("--check mode: nothing was started.")
        make_bug_report(base, log, scan, None, [])
        return EXIT_OK

    log.step("4/6 Pre-launch safety checks")
    procs = running_processes()
    if any(g.lower() in procs for g in GAME_EXES):
        log.error("Genshin is ALREADY running. 3DMigoto must start BEFORE the game, otherwise skins do not load. Close the game and start this again.")
        return EXIT_GAME_RUNNING
    if LOADER_PROC in procs:
        log.warn("A stale 3DMigoto Loader is running - closing it.")
        subprocess.run(["taskkill", "/f", "/im", LOADER_EXE], capture_output=True)
        _sleep(1)
    for f in ("d3d11.dll", "d3dx.ini"):
        if (game.parent / f).exists():
            log.warn("Found %s inside the game folder (%s). A leftover 3DMigoto copy there can conflict - delete it." % (f, game.parent))
    old = archive_log(mig_log, logs_dir, "previous", move=True)
    if old:
        log.info("Previous 3DMigoto log archived: %s" % old.name)
    log.info("OK")

    log.step("5/6 Configure and start")
    backup = ini_path.with_name("d3dx.ini.orig")
    if not backup.exists():
        shutil.copy2(ini_path, backup)
    debug = bool(cfg["debug_logging"] or args.debug)
    updates = {("Loader", "target"): str(game), ("Loader", "launch"): str(game),
               ("Logging", "unbuffered"): "1" if debug else "0", ("Logging", "crash"): "1" if debug else "0"}
    write_raw(ini_path, patch_ini(ini_text, updates))
    log.info("d3dx.ini updated (target/launch = %s, debug=%s). Original saved as d3dx.ini.orig" % (game.name, debug))
    rc, out = start_loader(mig, cfg["loader_timeout_seconds"], log)
    for l in out.splitlines():
        if l.strip():
            log.info("loader> " + l.strip())
    if rc not in (0, None):
        log.error("Loader exited with code %s - see 'loader>' lines above." % rc)
        return EXIT_LOADER

    log.step("6/6 Verify")
    wait = int(cfg["wait_for_game_seconds"])
    if not wait_for(lambda: game.name.lower() in running_processes(), wait):
        log.error("Game process did not appear within %ds. Check 'loader>' lines above." % wait)
        make_bug_report(base, log, scan, None, [])
        return EXIT_NO_START
    log.info("Game process is running.")
    logging_on = (get_ini_value(ini_text, "Logging", "calls") or "0") != "0" or (get_ini_value(ini_text, "Logging", "input") or "0") != "0"
    if logging_on:
        if wait_for(lambda: mig_log.exists(), wait):
            log.info("3DMigoto is LOADED in the game (its log file appeared). In game: press F10 to reload mods.")
        else:
            log.error("3DMigoto did NOT load into the game (no d3d11_log.txt after %ds). "
                      "Likely: antivirus blocked d3d11.dll, not running as admin, or wrong game path." % wait)
    else:
        log.info("3DMigoto logging is off in d3dx.ini, cannot verify loading automatically.")

    if cfg["stay_open_until_game_exits"]:
        log.info("Leave this window open; it archives the 3DMigoto log when you close the game.")
        wait_for(lambda: game.name.lower() not in running_processes(), 10 ** 9)
        log.info("Game closed.")
    archived = archive_log(mig_log, logs_dir, "session")
    summary = summarize_3dmigoto_log(archived) if archived else []
    if archived:
        log.info("3DMigoto log saved: %s" % archived.name)
    for l in summary[:30]:
        log.warn("3dmigoto-log> " + l)
    make_bug_report(base, log, scan, archived, summary)
    rotate_logs(logs_dir, int(cfg["keep_logs"]))
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="One-click Genshin + 3DMigoto launcher")
    ap.add_argument("--check", action="store_true", help="check files + scan mods only")
    ap.add_argument("--debug", action="store_true", help="extra 3DMigoto logging for this run")
    ap.add_argument("--no-pause", action="store_true", help="never wait for Enter at the end")
    args = ap.parse_args(argv)

    if os.name == "nt" and not args.check and not is_admin():
        if relaunch_as_admin():
            return 0
        print("Could not get admin rights. Right-click the exe -> Run as administrator.")
        input("Press Enter...")
        return EXIT_ERROR

    base = base_dir()
    log = Log(base / "logs" / ("launcher_%s.log" % dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
    try:
        rc = run(args, base, log)
    except Exception:
        log.error("Unexpected crash:\n" + traceback.format_exc())
        make_bug_report(base, log, None, None, [])
        rc = EXIT_ERROR
    log.info("Finished with code %d. Log: %s" % (rc, log.path))
    if rc != EXIT_OK and not args.no_pause and sys.stdin and sys.stdin.isatty():
        input("Something went wrong - see messages above. Press Enter to close...")
    return rc


if __name__ == "__main__":
    sys.exit(main())

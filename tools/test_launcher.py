"""Headless tests. Run:  python tools/test_launcher.py   (from the project root)"""
import sys, tempfile, shutil, types, zipfile, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import launcher as L

class Quiet:
    def __init__(s): s.lines = []; s.path = None
    def info(s, m): s.lines.append(("I", m))
    def warn(s, m): s.lines.append(("W", m))
    def error(s, m): s.lines.append(("E", m))
    def step(s, m): pass

def test_patch_ini():
    t = "[Logging]\r\ncalls=1\r\n[Loader]\r\ntarget = C:\\old.exe\r\n;launch = x.exe\r\nmodule = d3d11.dll\r\n"
    n = L.patch_ini(t, {("Loader", "target"): "D:\\g.exe", ("Loader", "launch"): "D:\\g.exe", ("Logging", "crash"): "0"})
    assert "target = D:\\g.exe" in n and "launch = D:\\g.exe" in n and "module = d3d11.dll" in n
    assert "calls=1" in n and "crash = 0" in n and "\r\n" in n and ";launch = x.exe" in n
    assert L.get_ini_value(n, "Loader", "target") == "D:\\g.exe"
    real = (ROOT / "3dmigoto" / "d3dx.ini").read_bytes().decode("utf-8", "surrogateescape")
    n2 = L.patch_ini(real, {("Loader", "target"): "X"})
    assert len(n2.splitlines()) == len(real.splitlines()) and L.get_ini_value(n2, "Loader", "module") == "d3d11.dll"

def test_scan_mods():
    d = Path(tempfile.mkdtemp()); m = d / "Mods"
    (m / "SkinA").mkdir(parents=True); (m / "SkinB").mkdir(); (m / "DISABLED_Old").mkdir()
    (m / "SkinA" / "a.ini").write_text("[TextureOverrideA]\nhash = abcd1234\n[ResourceA]\nfilename = a.buf\n")
    (m / "SkinA" / "a.buf").write_bytes(b"x")
    (m / "SkinB" / "b.ini").write_text("[TextureOverrideB]\nhash = ABCD1234\n[ResourceB]\nfilename = gone.dds ; c\n")
    (m / "DISABLED_Old" / "o.ini").write_text("[TextureOverrideO]\nhash = abcd1234\n")
    r = L.scan_mods(m)
    assert r["mods"] == ["SkinA", "SkinB"], r
    assert any("MISSING FILE" in w and "gone.dds" in w for w in r["warnings"]), r
    assert any("CONFLICT" in w for w in r["warnings"]), r
    assert not any("a.buf" in w for w in r["warnings"])
    shutil.rmtree(d)

def test_find_game():
    d = Path(tempfile.mkdtemp()); g = d / "Program Files" / "Genshin Impact" / "Genshin Impact Game"; g.mkdir(parents=True)
    (g / "GenshinImpact.exe").write_bytes(b"x")
    assert L.find_game(dict(L.DEFAULT_CONFIG), "", Quiet(), roots=[d], reg_dirs=[]) == g / "GenshinImpact.exe"
    assert L.find_game(dict(L.DEFAULT_CONFIG), "", Quiet(), roots=[], reg_dirs=[]) is None
    shutil.rmtree(d)

def test_log_summary():
    f = Path(tempfile.mkdtemp()) / "l.txt"
    f.write_text("ok line\nERROR: bad ini\nfine\nWarning: x not found\n")
    assert len(L.summarize_3dmigoto_log(f)) == 2

def full_flow(game_appears=True, dll_loads=True):
    base = Path(tempfile.mkdtemp()) / "proj"; base.mkdir()
    shutil.copytree(ROOT / "3dmigoto", base / "3dmigoto")
    g = base / "game"; g.mkdir(); (g / "GenshinImpact.exe").write_bytes(b"x")
    cfg = dict(L.DEFAULT_CONFIG, game_exe=str(g / "GenshinImpact.exe"), wait_for_game_seconds=1)
    (base / "launcher_config.json").write_text(json.dumps(cfg))
    state = {"loader": False, "polls": 0}
    def procs():
        if not state["loader"]: return []
        state["polls"] += 1
        return ["genshinimpact.exe"] if (game_appears and state["polls"] < 6) else []
    def loader(mig, timeout, log):
        state["loader"] = True
        if dll_loads: (mig / "d3d11_log.txt").write_text("start\nERROR: Unrecognised entry foo\n")
        return 0, "3DMigoto ready, launching\n"
    L.running_processes, L.start_loader, L._sleep = procs, loader, lambda s: None
    log = L.Log(base / "logs" / "t.log")
    rc = L.run(types.SimpleNamespace(check=False, debug=False), base, log)
    return rc, base

def test_flow_ok():
    rc, base = full_flow()
    assert rc == L.EXIT_OK
    assert "launch = %s" % (base / "game" / "GenshinImpact.exe") in (base / "3dmigoto" / "d3dx.ini").read_text()
    assert (base / "3dmigoto" / "d3dx.ini.orig").exists()
    z = zipfile.ZipFile(base / "logs" / "bug_report_latest.zip")
    assert "launcher.log" in z.namelist() and "3dmigoto_log.txt" in z.namelist()
    assert "Unrecognised" in z.read("3dmigoto_log_problems.txt").decode()

def test_flow_game_never_starts():
    rc, _ = full_flow(game_appears=False); assert rc == L.EXIT_NO_START

def test_flow_dll_not_loaded():
    rc, base = full_flow(dll_loads=False)
    assert "did NOT load" in (base / "logs" / "t.log").read_text()

def test_flow_game_already_running():
    base = Path(tempfile.mkdtemp()) / "p"; base.mkdir(); shutil.copytree(ROOT / "3dmigoto", base / "3dmigoto")
    g = base / "GenshinImpact.exe"; g.write_bytes(b"x")
    (base / "launcher_config.json").write_text(json.dumps(dict(L.DEFAULT_CONFIG, game_exe=str(g))))
    L.running_processes = lambda: ["genshinimpact.exe"]
    assert L.run(types.SimpleNamespace(check=False, debug=False), base, L.Log(base / "logs" / "x.log")) == L.EXIT_GAME_RUNNING


import subprocess, os
import mods as M

def _mk_archive(tmp, kind, tree):
    src = Path(tempfile.mkdtemp())
    for rel, data in tree.items():
        f = src / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(data)
    out = Path(tmp) / ("pack." + kind)
    if kind == "zip":
        with zipfile.ZipFile(out, "w") as z:
            for f in src.rglob("*"):
                if f.is_file(): z.write(f, f.relative_to(src).as_posix())
    else:
        seven = shutil.which("7z") or shutil.which("7za")
        assert seven, "7z needed for this test"
        subprocess.run([seven, "a", str(out), "."], cwd=src, capture_output=True, check=True)
    return out

def _proj():
    b = Path(tempfile.mkdtemp()); (b / "3dmigoto" / "Mods").mkdir(parents=True); return b

def test_install_variants():
    ok = {"[TextureOverrideX]\nhash = 11111111\n[ResourceX]\nfilename = x.buf\n"}
    ini = "[TextureOverrideX]\nhash = 11111111\n[ResourceX]\nfilename = x.buf\n"
    for kind in ("zip", "7z"):
        for tree, expect in (({"Keqing/k.ini": ini, "Keqing/x.buf": "b"}, "Keqing"),            # one top folder
                             ({"k.ini": ini, "x.buf": "b"}, "pack"),                             # files at root -> archive name
                             ({"Wrap/Inner/k.ini": ini, "Wrap/Inner/x.buf": "b"}, "Inner")):     # nested wrappers
            b = _proj(); a = _mk_archive(tempfile.mkdtemp(), kind, tree)
            name = M.install_mod(a, b)
            assert name == expect, (kind, tree.keys(), name)
            assert (M.mods_dir(b) / name / "x.buf").exists() and not M.staging_dir(b).exists()
            assert L.scan_mods(M.mods_dir(b))["warnings"] == []

def test_install_errors_and_replace():
    b = _proj()
    a = _mk_archive(tempfile.mkdtemp(), "zip", {"readme.txt": "hi"})
    try: M.install_mod(a, b); assert False
    except M.ModError as e: assert "no .ini" in str(e)
    bad = Path(tempfile.mkdtemp()) / "bad.rar"; bad.write_bytes(b"not a rar")
    try: M.install_mod(bad, b); assert False
    except M.ModError: pass
    txt = Path(tempfile.mkdtemp()) / "a.txt"; txt.write_text("x")
    try: M.install_mod(txt, b); assert False
    except M.ModError: pass
    z = _mk_archive(tempfile.mkdtemp(), "zip", {"K/k.ini": "[A]\n"})
    M.install_mod(z, b); M.install_mod(z, b)
    assert len(list(M.trash_dir(b).iterdir())) == 1 and len(M.list_mods(b)) == 1

def test_zip_slip():
    b = _proj(); z = Path(tempfile.mkdtemp()) / "evil.zip"
    with zipfile.ZipFile(z, "w") as f: f.writestr("../evil.ini", "x")
    try: M.install_mod(z, b); assert False
    except M.ModError as e: assert "unsafe" in str(e)

def test_toggle_remove_list():
    b = _proj(); m = M.mods_dir(b) / "SkinA"; m.mkdir(); (m / "a.ini").write_text("[A]\nhash=1\n")
    assert M.set_enabled(b, "SkinA", False) == "DISABLED_SkinA"
    info = M.list_mods(b)[0]
    assert info["name"] == "SkinA" and not info["enabled"] and info["ini_count"] == 1
    assert M.set_enabled(b, "DISABLED_SkinA", True) == "SkinA"
    M.remove_mod(b, "SkinA"); assert M.list_mods(b) == [] and len(list(M.trash_dir(b).iterdir())) == 1

def test_by_mod_conflict():
    b = _proj(); d = M.mods_dir(b)
    for n in ("A", "B"):
        (d / n).mkdir(); (d / n / "m.ini").write_text("[TextureOverrideT]\nhash = deadbeef\n")
    r = L.scan_mods(d); assert "A" in r["by_mod"] and "B" in r["by_mod"]
    assert M.list_mods(b, r["by_mod"])[0]["warnings"]


def test_core_problems_and_update():
    b = _proj(); mig = b / "3dmigoto"
    (mig / "d3d11.dll").write_bytes(b"OLD-DLL"); (mig / "d3dx.ini").write_text("[Loader]\ntarget = x\n")
    (mig / "ShaderFixes").mkdir(); (mig / "ShaderFixes" / "old.hlsl").write_text("o")
    m = M.mods_dir(b) / "Zhongli"; m.mkdir()
    (m / "z.ini").write_text("[A]\noverride_vertex_count = 5\nrun = x\n[B]\npre resource\\shaderfixes\\help.ini\\n = y\n")
    scan = L.scan_mods(M.mods_dir(b))
    assert scan["needs"]["override_vertex_count"] == ["Zhongli"]
    probs = L.core_problems(mig, scan)
    assert len(probs) == 2 and "OUTDATED" in probs[0] and "Zhongli" in probs[0], probs
    z = _mk_archive(tempfile.mkdtemp(), "zip", {"Pkg/d3d11.dll": "NEW override_vertex_count", "Pkg/d3dx.ini": "[Loader]\n",
                                                "Pkg/ShaderFixes/help.ini": "h", "Pkg/Mods/readme.txt": "r"})
    (mig / "d3dx.ini.orig").write_text("old")
    bak = M.install_core(z, b)
    assert (mig / "d3d11.dll").read_text().startswith("NEW") and (bak / "d3d11.dll").read_bytes() == b"OLD-DLL"
    assert (mig / "ShaderFixes" / "help.ini").exists() and (mig / "Mods" / "Zhongli" / "z.ini").exists()
    assert not (mig / "d3dx.ini.orig").exists() and not (bak / "Mods").exists()
    assert L.core_problems(mig, scan) == []
    bad = _mk_archive(tempfile.mkdtemp(), "zip", {"a/b.txt": "x"})
    try: M.install_core(bad, b); assert False
    except M.ModError as e: assert "not a 3DMigoto package" in str(e)


def test_orfix_system_folder_and_fix_tools():
    b = _proj(); d = M.mods_dir(b); (b / "3dmigoto" / "d3d11.dll").write_bytes(b"dll")
    (d / "Zhongli").mkdir(); (d / "Zhongli" / "z.ini").write_text("[A]\nrun = commandlist\\global\\orfix\\nnfix\n")
    scan = L.scan_mods(d)
    probs = L.core_problems(b / "3dmigoto", scan)
    assert any(p.startswith("MISSING ORFIX") for p in probs), probs
    assert not M.orfix_installed(b)
    (d / "BufferValues").mkdir(); (d / "BufferValues" / "ORFix.ini").write_text("[TextureOverrideGaming]\nhash = 8b5fc47d\n")
    (d / "Other").mkdir(); (d / "Other" / "o.ini").write_text("[TextureOverrideX]\nhash = 8b5fc47d\n")
    scan = L.scan_mods(d)
    assert M.orfix_installed(b) and not any(p.startswith("MISSING ORFIX") for p in L.core_problems(b / "3dmigoto", scan))
    assert scan["warnings"] == [] and [m["name"] for m in M.list_mods(b)] == ["Other", "Zhongli"]
    (d / "gifixversion72.exe").write_bytes(b"x"); (d / "orfixapplier_ver_4_2_e863a.exe").write_bytes(b"x")
    tools = M.find_fix_tools(b); assert [t[1].name for t in tools] == ["gifixversion72.exe", "orfixapplier_ver_4_2_e863a.exe"]
    started = []
    M.subprocess.Popen = lambda *a, **k: started.append((a, k))
    bak = M.run_fix_tool(b, tools[0][1])
    assert (bak / "Zhongli" / "z.ini").exists() and (bak / "BufferValues" / "ORFix.ini").exists() and started

if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1; print("PASS", k)
    print("%d tests passed" % n)

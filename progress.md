# GenshinModLauncher - progress log (READ THIS FIRST when resuming)
Last updated: 2026-11-13 (milestone 4)

## DESIGN RULES
- User: a player who downloads skin mods only as .rar; must never extract by hand; wants a clean window with all options visible.
- Main window: LAUNCH button (top), status line (game / RAR support / mod counts), mod list with buttons on the right, details line, live log, bottom bar (logs, bug report, rescan, settings). Rare options (game path, debug) only in Settings.
- Flows: Add mod -> extract to Mods_staging -> find real mod root -> move to Mods/<Name> (same name replaces, old one to Mods_trash). Toggle = rename folder DISABLED_<Name>. Remove = move to Mods_trash (never delete).
- Errors: every failure is a message box + red log line; missing extractor says to install 7-Zip; first run / no mods shows a hint text.
- Safety: zip-slip check on ZIP; extraction only into Mods_staging; archives never executed. NOTE: drag&drop is NOT offered because the app runs as admin and Windows blocks drops from non-admin Explorer.
- Naming: "mod", "enable/disable", "Add mod", "Launch" used the same everywhere.

## CURRENT STATE
- M4: User's YouTuber tutorial files added to the build exactly where he put them: `3dmigoto/Mods/BufferValues/ORFix.ini` (v0.9.21, defines CommandList\global\ORFix\ORFix + NNFix; plain ini, reviewed: no includes/keys/exe refs), `3dmigoto/Mods/gifixversion72.exe` and `3dmigoto/Mods/orfixapplier_ver_4_2_e863a.exe` (PyInstaller/Python 3.12 console tools; NOT verified/virus-scanned by me; sha256 gifix=4bd98b98...8f0b, orfixapplier=b722adc8...7ac7f96; ORFix.ini header says official source is github.com/LeoTorreZ/LeoTools).
- ORFix.ini only fixes the 'commandlist\global\orfix\nnfix' warnings. 'override_vertex_count / override_byte_stride' still need the newer 3DMigoto d3d11.dll (Update 3DMigoto button).
- GUI: 'ORFix: check/missing' in status line; 'Fix tools' menu runs GIFix / ORFix Applier in their own console in Mods after backing up ALL mod .ini files to backups/mods_ini_before_fix_<time>. (Backup is .ini only - assumption: tools edit ini files; unverified.) Untested on Windows.
- `BufferValues` is a SYSTEM folder (mods.SYSTEM_FOLDERS): hidden from mod list, skipped by scan_mods (its hashes would give false conflicts). launcher.core_problems also reports MISSING ORFIX if a mod calls ORFix and no ORFix.ini exists.
- M3: FIRST REAL RUN on user's Windows PC: GUI works, game found (D:\Application Data\Genshin Impact\GenshinImpact\games\Genshin Impact game\GenshinImpact.exe), 7z found. Launch showed 3DMigoto on-screen warnings: "Unrecognised entry: override_vertex_count / override_byte_stride / run = commandlist\global\orfix\nnfix / pre resource\shaderfixes\help.ini\..." for mod ArchonWarZhongli. CAUSE (verified in d3d11.dll): bundled 3DMigoto is v1.3.16 (old SilentNightSound 'for playing mods' build) without those keys; ShaderFixes\help.ini missing. Mod needs the newer GIMI package.
- M3 fix: launcher.core_problems() detects this (scan_mods 'needs'), shows red status line + log; GUI button 'Update 3DMigoto' -> mods.install_core(): extracts a package zip/rar/7z, backs up old files to backups/3dmigoto_before_update_<time>, copies new files over 3dmigoto/, keeps Mods, deletes d3dx.ini.orig. Could NOT download the newer package myself (GitHub releases blocked in sandbox) - user must download it (GIMI-Package releases, 'for playing mods' zip) and use the button.
- Also seen: mod ArchonWarZhongli has 2 MISSING .dds (ZhongliFaceHeadLightMap/Shadow) - mod incomplete, not an extraction bug.
- M2: GUI added (tkinter): src/gui.py (entry of exe, --windowed --uac-admin), src/mods.py (install/list/toggle/remove, extractor finder: bundled tools/7z.exe -> installed 7-Zip -> WinRAR UnRAR -> tar), launcher.py unchanged flow + Log listeners + per-mod warnings (scan_mods 'by_mod').
- CI bundles 7z.exe/7z.dll from the Windows runner into tools/ (RAR support, no install needed).
- Sandbox verified: 13 headless tests (zip + 7z install variants, nested folders, no-ini, bad archive, zip-slip, replace->trash, toggle/remove, conflicts), GUI rendered under Xvfb and checked by screenshot.
- NOT verified: real .rar extraction (no RAR creator in sandbox; same 7z command path as 7z archives), Windows rendering of the window, UAC, real launch, CI build.
- Base: user's existing stock 3DMigoto package (SilentNightSound GI build) in `3dmigoto/` - NO source existed, only binaries + d3dx.ini.
- NEW `src/launcher.py` (Python, PyInstaller `--onefile --uac-admin` via CI): admin -> file check -> find game -> mod scan -> patch d3dx.ini (target/launch) -> run loader (loader launches Genshin) -> verify game process + 3DMigoto log -> archive logs -> `logs/bug_report_latest.zip`.
- Sandbox-tested (Linux, 8 headless tests incl. full flow with mocked loader/processes): ini patching, mod scan, game finding, log summary, flow success/failure paths.
- NOT tested: real Windows run, real loader, real game, UAC, registry lookup, PyInstaller build/CI. First real run may need small fixes -> send bug_report_latest.zip.

## DECISIONS
- User wants: open launcher -> Genshin opens automatically; strong against failures; logs for bug fixing; clean standard structure.
- Auto-launch uses the loader's own `launch =` option (not a hand-rolled process start).
- Python + PyInstaller, built by GitHub Actions (user's usual workflow). Windows/cmd instructions only.

## RULES / DO NOT BREAK
- 3DMigoto MUST start before the game (launcher refuses if Genshin already running) - otherwise skins don't load.
- d3dx.ini: launcher only changes [Loader] target/launch and [Logging] unbuffered/crash. Original saved once as d3dx.ini.orig. Never touch other keys (modified for Genshin compat by upstream).
- `[Logging] calls=1`/`input=1` shipped as-is; verification of "3DMigoto loaded" relies on d3d11_log.txt appearing (only checked if calls or input is on). Unverified assumption: calls=0 might stop the log being created; do not change without testing.
- Files are read/written as UTF-8 with surrogateescape (ini may be ANSI); console printing never crashes on Unicode mod names.
- Mods folders named DISABLED* are ignored (matches d3dx.ini exclude_recursive).
- Internal ids: exe name `GenshinModLauncher`, config `launcher_config.json`, `logs/` - don't rename.

## MAP
- src/gui.py - window (App class); src/mods.py - mod manager (no GUI code); src/launcher.py - launch flow, Log, config, ini patch, find_game, scan_mods, loader, CLI main
- 3dmigoto/ - stock 3DMigoto (d3d11.dll, loader, d3dx.ini, ShaderFixes); Mods/ created at runtime
- tools/test_launcher.py - headless tests
- .github/workflows/build.yml - tests -> PyInstaller -> artifact (tags v* -> Release)
- Runtime (not in repo): launcher_config.json, logs/launcher_*.log, logs/3dmigoto_*.txt, logs/bug_report_latest.zip

## TESTS
`python tools/test_launcher.py` from project root. Mocks `running_processes`, `start_loader`, `_sleep`.

## WORKFLOW + PREFS
- User pushes whole project to GitHub, downloads artifact. Wants full progress.zip + progress.md each time, short replies, honest about what's untested.
- Pacing rule: stop after ~10 tool calls / 5 min, deliver zip + progress.md.

## REMAINING WORK
00. User updates 3DMigoto via button, relaunches, sends bug_report_latest.zip. If still warnings: check d3dx.ini of new package vs our patch (target/launch/unbuffered/crash only).
0. (done in M3) User checks the window on real Windows + tries a real .rar mod; fix what breaks.
1. User runs it on real Windows; report the first bug_report_latest.zip (confirm UAC, game detect, loader `launch=` with full path, log verification).
2. Confirm HoYoPlay/registry install paths; add if autodetect misses.
3. Decide if `calls=0` is safe (performance) after confirming the log still appears.
4. Optional: in-game-event parsing of 3DMigoto log for mod-load confirmation; GUI/tray status.
5. Optional: auto-update hash conflicts help (suggest which mod to disable).

---
# HISTORY
- M4 (2026-11-13): Added ORFix.ini + GIFix + ORFix Applier to the build, Fix tools menu with ini backup, ORFix status/check. 15/15 tests.
- M3 (2026-11-13): Real run revealed outdated 3DMigoto vs modern mods. Added outdated-core detection + Update 3DMigoto button/installer. 14/14 tests.
- M2 (2026-11-13): Added GUI mod manager (list, add RAR/ZIP/7z, enable/disable, remove, live log, bug report, settings), bundled 7-Zip in CI. 13/13 tests, screenshot-checked layout (fixed clipped buttons/bottom bar).
- M1 (2026-11-13): Project created around user's stock 3DMigoto zip. Added launcher, logging, mod scan, CI, tests. Sandbox tests pass (8/8). Review: fixed backslash path split bug and a hacky wait loop; no real-Windows run yet.

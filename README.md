# GenshinModLauncher

One window to manage 3DMigoto mods for Genshin Impact and start the game with them.

## Get it
Push to GitHub -> Actions -> download artifact `GenshinModLauncher` (tags `v*` also make a Release). Unzip anywhere.
Contents: `GenshinModLauncher.exe`, `3dmigoto\`, `tools\7z.exe` (opens RAR/7z/ZIP, nothing to install).

## Use
1. Double-click `GenshinModLauncher.exe`, accept the admin prompt.
2. **Add mod**: click `Add mod (RAR / ZIP / 7z)`, pick one or several archives. They are extracted and installed for you (nested folders are fixed). Never extract by hand.
3. **Mod list**: shows every mod with On/Off, number of .ini files, size and status. Double-click (or `Enable / Disable`) turns a mod on/off. `Remove` moves it to `3dmigoto\Mods_trash` (recoverable). Orange rows have problems: select one to read them (missing file, two skins using the same hash, no .ini).
4. **LAUNCH GENSHIN**: closes nothing, starts 3DMigoto, then the game, verifies 3DMigoto loaded. In game press F10 to apply mod changes. Keep the window open; it saves logs when the game closes.
5. Only one skin per character should be On.

## Fixes for newer game versions
`3dmigoto\Mods\BufferValues\ORFix.ini` is required by many current mods (ORFix commands) - do not delete it. `Fix tools` (bottom row) runs `gifixversion72.exe` and `orfixapplier_ver_4_2_e863a.exe` (community tools, from the LeoTools project) on your mods; a backup of all mod .ini files is made first. Mods that need a newer 3DMigoto: use `Update 3DMigoto` with the latest GIMI package.

## When something is wrong
Click `Create bug report` -> send `logs\bug_report_latest.zip`. `Open logs folder` shows all logs. Settings: game path, extra debug logging.
Console mode: `GenshinModLauncher.exe --check` (checks mods, starts nothing; result is in logs\).

## Develop
`python tools/test_launcher.py` (headless tests). `python src/gui.py` runs the window. Files: `src/gui.py` window, `src/mods.py` install/list/toggle, `src/launcher.py` launch flow + logs. See progress.md.

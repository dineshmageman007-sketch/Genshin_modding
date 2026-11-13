"""Genshin Mod Launcher - main window (entry point of the exe).

Window: [Launch button] / status line / mod list + buttons / details / live log + buttons.
All logic lives in launcher.py (start game, logs) and mods.py (install, enable, remove).
Run:  python src/gui.py          (GUI)
      python src/gui.py --check  (console-less dry run, same as launcher.py --check)
"""
import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import types
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import launcher as L
import mods as M

BG, HEAD, GREEN, GREEN_DK = "#f3f4f6", "#1f2937", "#16a34a", "#15803d"


class _Null:
    def info(self, m): pass
    warn = error = step = info


class App(tk.Tk):
    def __init__(self, base):
        super().__init__()
        self.base = Path(base)
        self.q = queue.Queue()
        self.running = False
        self._warned = set()
        self.scan = {"mods": [], "warnings": [], "by_mod": {}}
        self.title("Genshin Mod Launcher")
        self.geometry("980x760")
        self.minsize(900, 640)
        self.configure(bg=BG)
        self.log = L.Log(self.base / "logs" / ("launcher_%s.log" % dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
        self.log.listeners.append(lambda lvl, line: self.q.put(lambda: self._append_log(lvl, line)))
        self.cfg = L.load_config(self.base / "launcher_config.json", self.log)
        self._build()
        self.refresh()
        self.after(100, self._poll)
        self.log.info("Ready. Add mods with 'Add mod', then press LAUNCH.")

    # ------------------------------------------------------------------ layout
    def _build(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        st.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        st.configure("TLabelframe", background=BG)
        st.configure("TLabelframe.Label", background=BG, font=("Segoe UI", 10, "bold"))

        head = tk.Frame(self, bg=HEAD)
        head.pack(fill="x")
        t = tk.Frame(head, bg=HEAD)
        t.pack(side="left", padx=18, pady=12)
        tk.Label(t, text="Genshin Mod Launcher", bg=HEAD, fg="white", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(t, text="3DMigoto starts first, then the game - skins load properly", bg=HEAD, fg="#9ca3af",
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.launch_btn = tk.Button(head, text="▶  LAUNCH GENSHIN", command=self.launch, bg=GREEN, fg="white",
                                    activebackground=GREEN_DK, activeforeground="white", relief="flat", bd=0,
                                    font=("Segoe UI", 14, "bold"), padx=28, pady=10, cursor="hand2")
        self.launch_btn.pack(side="right", padx=18, pady=12)

        self.status = tk.Label(self, text="", bg=BG, anchor="w", font=("Segoe UI", 9), justify="left")
        self.status.pack(fill="x", padx=18, pady=(8, 0))

        mid = ttk.LabelFrame(self, text=" Your mods ")
        mid.pack(fill="both", expand=True, padx=14, pady=(8, 4))
        left = tk.Frame(mid, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        cols = ("on", "name", "files", "size", "state")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse", height=9)
        for c, txt, w, a in (("on", "On", 46, "center"), ("name", "Mod", 260, "w"), ("files", ".ini", 56, "center"),
                             ("size", "Size", 90, "e"), ("state", "Status", 230, "w")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("name", "state")))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("off", foreground="#9ca3af")
        self.tree.tag_configure("warn", foreground="#b45309")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._details())
        self.tree.bind("<Double-1>", lambda e: self.toggle())
        self.tree.bind("<space>", lambda e: self.toggle())
        self.tree.bind("<Delete>", lambda e: self.remove())

        btns = tk.Frame(mid, bg=BG)
        btns.pack(side="right", fill="y", padx=(4, 8), pady=8)
        for text, cmd in (("＋  Add mod (RAR / ZIP / 7z)", self.add_mod), ("Enable / Disable", self.toggle),
                          ("Remove", self.remove), ("Open Mods folder", lambda: self._open(M.mods_dir(self.base))),
                          ("Rescan", self.refresh)):
            ttk.Button(btns, text=text, command=cmd, width=26).pack(fill="x", pady=3, ipady=4)
        tk.Label(btns, text="Double-click a mod\nto turn it on/off.\nIn game press F10\nto apply changes.", bg=BG,
                 fg="#6b7280", font=("Segoe UI", 8), justify="left").pack(side="bottom", anchor="w", pady=6)

        self.details = tk.Label(self, text="", bg=BG, fg="#374151", anchor="w", justify="left", wraplength=920,
                                font=("Segoe UI", 9))
        self.details.pack(fill="x", padx=18)

        bar = tk.Frame(self, bg=BG)
        bar.pack(side="bottom", fill="x", padx=14, pady=(0, 10))
        for text, cmd in (("Open logs folder", lambda: self._open(self.base / "logs")),
                          ("Create bug report", self.bug_report), ("Update 3DMigoto", self.update_core), ("Fix tools \u25BE", self.fix_tools),
                          ("Settings", self.settings)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6), ipady=2)

        lf = ttk.LabelFrame(self, text=" Log ")
        lf.pack(fill="both", expand=True, padx=14, pady=(6, 4))
        self.logbox = tk.Text(lf, height=7, state="disabled", wrap="word", bg="white", font=("Consolas", 9), relief="flat")
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y", pady=6)
        self.logbox.pack(fill="both", expand=True, padx=(8, 0), pady=6)
        self.logbox.tag_configure("WARN", foreground="#b45309")
        self.logbox.tag_configure("ERROR", foreground="#dc2626")
        self.logbox.tag_configure("STEP", foreground="#2563eb")

    # ------------------------------------------------------------------ helpers
    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append_log(self, lvl, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n", lvl.strip())
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def bg(self, work, done=None):
        """Run work() in a thread; done(result, error) runs in the UI thread."""
        def target():
            try:
                res, err = work(), None
            except Exception as e:
                res, err = None, e
                if not isinstance(e, M.ModError):
                    self.log.error(traceback.format_exc())
            if done:
                self.q.put(lambda: done(res, err))
        threading.Thread(target=target, daemon=True).start()

    def _open(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log.warn("Could not open %s (%s)" % (path, e))

    def _selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    # ------------------------------------------------------------------ mod list
    def refresh(self):
        keep = self._selected()
        self.scan = L.scan_mods(M.mods_dir(self.base)) if not self.cfg.get("skip_mod_check") else self.scan
        self.tree.delete(*self.tree.get_children())
        listing = M.list_mods(self.base, self.scan.get("by_mod", {}))
        for m in listing:
            if not m["enabled"]:
                state, tag = "Disabled", "off"
            elif m["ini_count"] == 0:
                state, tag = "⚠ No .ini file - not a mod?", "warn"
            elif m["warnings"]:
                state, tag = "⚠ %d problem(s) - select to see" % len(m["warnings"]), "warn"
            else:
                state, tag = "OK", ""
            self.tree.insert("", "end", iid=m["folder"], tags=(tag,) if tag else (),
                             values=("✔" if m["enabled"] else "—", m["name"], m["ini_count"],
                                     M.human_size(m["size"]), state))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        on = sum(1 for m in listing if m["enabled"])
        game = L.find_game(self.cfg, L.get_ini_value(L.read_raw(self.base / "3dmigoto" / "d3dx.ini"), "Loader", "target")
                           if (self.base / "3dmigoto" / "d3dx.ini").exists() else "", _Null())
        ex = M.find_extractor(self.base)
        self.game = game
        self.core = L.core_problems(self.base / "3dmigoto", self.scan)
        for p in self.core:
            if p not in self._warned:
                self._warned.add(p)
                self.log.error(p)
        self.status.configure(
            text="Game: %s      RAR support: %s      ORFix: %s      Mods: %d on / %d total"
                 % (("✔ " + str(game)) if game else "✖ not found - open Settings",
                    ("✔ " + ex[0]) if ex else "✖ missing (install 7-Zip)",
                    "✔" if M.orfix_installed(self.base) else "✖ missing", on, len(listing)),
            fg="#166534" if game and ex and not self.core else "#b91c1c")
        if self.core:
            hints = {"OUTDATED": "3DMigoto is too old for your mods - click 'Update 3DMigoto' (bottom) and choose the newest GIMI package.",
                     "MISSING ORFIX": "ORFix.ini is missing - put it in 3dmigoto\\Mods\\BufferValues."}
            for p in self.core:
                self.status.configure(text=self.status.cget("text") + "\n\u26A0 " + next((v for k, v in hints.items() if p.startswith(k)), p))
        self._details()

    def _details(self):
        f = self._selected()
        if not f:
            self.details.configure(text="Select a mod to see details." if self.tree.get_children() else
                                   "No mods yet. Click 'Add mod' and choose a .rar file.")
            return
        m = next((x for x in M.list_mods(self.base, self.scan.get("by_mod", {})) if x["folder"] == f), None)
        if not m:
            return
        txt = "\n".join(m["warnings"]) if m["warnings"] else ("Turned off - 3DMigoto ignores this mod." if not m["enabled"] else "No problems found.")
        self.details.configure(text="%s:  %s" % (m["name"], txt))

    def toggle(self):
        f = self._selected()
        if not f:
            return messagebox.showinfo("Mods", "Select a mod in the list first.")
        try:
            new = M.set_enabled(self.base, f, M.is_disabled(f))
            self.log.info("%s: %s. In game press F10 to apply." % (M.plain_name(new), "disabled" if M.is_disabled(new) else "enabled"))
        except M.ModError as e:
            return messagebox.showerror("Mods", str(e))
        self.refresh()
        if self.tree.exists(new):
            self.tree.selection_set(new)

    def remove(self):
        f = self._selected()
        if not f:
            return messagebox.showinfo("Mods", "Select a mod in the list first.")
        if not messagebox.askyesno("Remove mod", "Remove '%s'?\n\nIt is moved to 3dmigoto\\Mods_trash, not deleted." % M.plain_name(f)):
            return
        try:
            dest = M.remove_mod(self.base, f)
            self.log.info("Removed %s (moved to %s)" % (f, dest.name))
        except M.ModError as e:
            messagebox.showerror("Mods", str(e))
        self.refresh()

    def add_mod(self):
        files = filedialog.askopenfilenames(title="Choose mod archive(s)", filetypes=[
            ("Mod archives", "*.rar *.zip *.7z"), ("All files", "*.*")])
        if not files:
            return

        def work():
            names = []
            for f in files:
                try:
                    names.append(M.install_mod(f, self.base, self.log))
                except M.ModError as e:
                    self.log.error("%s: %s" % (Path(f).name, e))
                    self.q.put(lambda f=f, e=e: messagebox.showerror("Could not add mod", "%s\n\n%s" % (Path(f).name, e)))
            return names

        def done(names, err):
            self.refresh()
            if names and self.tree.exists(names[-1]):
                self.tree.selection_set(names[-1])
            if names:
                self.log.info("%d mod(s) added. If the game is open, press F10 in game." % len(names))
        self.log.info("Adding %d archive(s)..." % len(files))
        self.bg(work, done)

    # ------------------------------------------------------------------ launch / logs / settings
    def launch(self):
        if self.running:
            return
        self.cfg = L.load_config(self.base / "launcher_config.json", self.log)
        self.running = True
        self.launch_btn.configure(state="disabled", text="RUNNING...  (finishes when game closes)", bg="#6b7280")
        args = types.SimpleNamespace(check=False, debug=False)

        def done(rc, err):
            self.running = False
            self.launch_btn.configure(state="normal", text="▶  LAUNCH GENSHIN", bg=GREEN)
            if err or rc != L.EXIT_OK:
                messagebox.showwarning("Launch problem", "Genshin did not start correctly.\nRead the red/orange lines in the log below.\n"
                                       "Use 'Create bug report' and send me the zip.")
            self.refresh()
        self.bg(lambda: L.run(args, self.base, self.log), done)

    def fix_tools(self):
        tools = M.find_fix_tools(self.base)
        menu = tk.Menu(self, tearoff=0)
        if not tools:
            menu.add_command(label="No fix tools found in 3dmigoto\\Mods", state="disabled")
        for label, exe in tools:
            menu.add_command(label=label, command=lambda e=exe: self._run_fix(e))
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery() - 20)

    def _run_fix(self, exe):
        if not messagebox.askyesno("Fix tool", "Run %s?\n\nIt opens in its own window and edits your mod .ini files.\n"
                                   "A backup of all mod .ini files is saved first in the 'backups' folder.\n"
                                   "Close Genshin before running it." % exe.name):
            return
        try:
            M.run_fix_tool(self.base, exe, self.log)
        except (M.ModError, OSError) as e:
            messagebox.showerror("Fix tool", str(e))

    def update_core(self):
        if self.running or L.game_is_running():
            return messagebox.showwarning("Update 3DMigoto", "Close Genshin (and wait for the launcher to finish) first.")
        f = filedialog.askopenfilename(title="Choose the 3DMigoto / GIMI package (zip, rar, 7z)",
                                       filetypes=[("Packages", "*.rar *.zip *.7z"), ("All files", "*.*")])
        if not f:
            return
        if not messagebox.askyesno("Update 3DMigoto", "Replace the 3DMigoto files (d3d11.dll, loader, d3dx.ini, ShaderFixes...) with the ones in\n%s\n\n"
                                   "Your mods are kept. The old files are saved in the 'backups' folder." % Path(f).name):
            return
        self.log.info("Updating 3DMigoto from %s ..." % Path(f).name)

        def done(bak, err):
            if err:
                messagebox.showerror("Update 3DMigoto", str(err))
            else:
                messagebox.showinfo("Update 3DMigoto", "Done. Old files saved in:\n%s\n\nNow press LAUNCH." % bak)
            self._warned.clear()
            self.refresh()
        self.bg(lambda: M.install_core(f, self.base, self.log), done)

    def bug_report(self):
        logs = self.base / "logs"
        sess = sorted(logs.glob("3dmigoto_*.txt"), key=lambda p: p.stat().st_mtime) if logs.exists() else []
        arch = sess[-1] if sess else None
        L.make_bug_report(self.base, self.log, self.scan, arch, L.summarize_3dmigoto_log(arch) if arch else [])
        self._open(logs)

    def settings(self):
        w = tk.Toplevel(self)
        w.title("Settings")
        w.configure(bg=BG)
        w.transient(self)
        w.grab_set()
        tk.Label(w, text="Path to GenshinImpact.exe (empty = find automatically)", bg=BG).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 2))
        game = tk.StringVar(value=self.cfg.get("game_exe", ""))
        tk.Entry(w, textvariable=game, width=62).grid(row=1, column=0, padx=(12, 4), pady=2)
        ttk.Button(w, text="Browse...", command=lambda: game.set(filedialog.askopenfilename(
            filetypes=[("Genshin", "GenshinImpact.exe YuanShen.exe"), ("exe", "*.exe")]) or game.get())).grid(row=1, column=1, padx=(0, 12))
        dbg = tk.BooleanVar(value=bool(self.cfg.get("debug_logging")))
        tk.Checkbutton(w, text="Extra detailed 3DMigoto logging (only when hunting a bug; slower)", variable=dbg, bg=BG).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=12, pady=6)

        def save():
            p = self.base / "launcher_config.json"
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                data = dict(L.DEFAULT_CONFIG)
            data["game_exe"], data["debug_logging"] = game.get().strip(), dbg.get()
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.cfg = L.load_config(p, self.log)
            w.destroy()
            self.refresh()
        ttk.Button(w, text="Save", command=save).grid(row=3, column=1, sticky="e", padx=12, pady=12)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in argv for a in ("--check", "--cli", "--debug")):
        return L.main([a for a in argv if a != "--cli"])
    if os.name == "nt" and not L.is_admin():
        if L.relaunch_as_admin():
            return 0
    base = L.base_dir()
    try:
        App(base).mainloop()
    except Exception:
        crash = base / "logs"
        crash.mkdir(exist_ok=True)
        (crash / "gui_crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("Genshin Mod Launcher", "The window crashed. Details: logs\\gui_crash.txt")
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

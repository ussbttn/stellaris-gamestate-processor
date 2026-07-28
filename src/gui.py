"""
gui.py — the window you double-click.

Stdlib only (tkinter), so PyInstaller bundles it with no extra dependencies.

Design assumption: the person using this does not use a terminal. Paths are
auto-detected and offered as defaults, settings are remembered, errors say what
to do rather than what went wrong internally.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from watcher import emit, newest_save  # noqa: E402

APP = "Stellaris Advisor"
CONFIG = Path(os.environ.get("APPDATA", Path.home())) / "StellarisAdvisor" / "config.json"


# --------------------------------------------------------------------------
# path guessing — the user should rarely have to browse
# --------------------------------------------------------------------------

def guess_saves() -> str:
    for base in (Path.home() / "Documents", Path.home() / "OneDrive" / "Documents"):
        p = base / "Paradox Interactive" / "Stellaris" / "save games"
        if p.exists():
            return str(p)
    return ""


def guess_sillytavern() -> str:
    """Find SillyTavern's extension folder, including the user handle."""
    candidates = [
        Path.home() / "SillyTavern",
        Path("C:/SillyTavern"),
        Path.home() / "Documents" / "SillyTavern",
        Path.home() / "Downloads" / "SillyTavern",
    ]
    for st in candidates:
        data = st / "data"
        if not data.exists():
            continue
        # data/<user handle>/extensions — handle is usually 'default-user'
        for user in sorted(data.iterdir()):
            ext = user / "extensions"
            if ext.exists():
                return str(ext / "stellaris-advisor")
    return ""


def load_config() -> dict:
    try:
        import json
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        import json
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=1))
    except Exception:
        pass  # never let a config write failure break the app


# --------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.msgs: queue.Queue[str] = queue.Queue()
        self.running = False
        self.thread: threading.Thread | None = None

        cfg = load_config()
        root.title(APP)
        root.geometry("720x460")
        root.minsize(640, 420)

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Watches your Stellaris saves and writes a briefing for SillyTavern.",
        ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        self.saves = self._path_row(
            frm, 1, "Stellaris save folder",
            cfg.get("saves") or guess_saves(),
        )
        self.out = self._path_row(
            frm, 2, "SillyTavern extension folder",
            cfg.get("out") or guess_sillytavern(),
        )

        self.btn = ttk.Button(frm, text="Start", command=self.toggle, width=14)
        self.btn.grid(row=3, column=0, sticky="w", **pad)

        self.status = ttk.Label(frm, text="Ready.")
        self.status.grid(row=3, column=1, columnspan=2, sticky="w", **pad)

        self.log = tk.Text(frm, height=16, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=4, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(4, weight=1)
        frm.columnconfigure(1, weight=1)

        for label, hint in (
            ("Stellaris save folder", "usually Documents\\Paradox Interactive\\Stellaris\\save games"),
            ("SillyTavern extension folder", "SillyTavern\\data\\default-user\\extensions\\stellaris-advisor"),
        ):
            if not (self.saves.get() if "save" in label else self.out.get()):
                self.write(f"Could not find your {label.lower()} — {hint}")

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self.drain)

    def _path_row(self, frm, row, label, initial) -> tk.StringVar:
        var = tk.StringVar(value=initial)
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(frm, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(
            frm, text="Browse…",
            command=lambda: self._browse(var),
        ).grid(row=row, column=2, sticky="e", padx=12, pady=4)
        return var

    def _browse(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)

    def write(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def drain(self) -> None:
        while not self.msgs.empty():
            self.write(self.msgs.get_nowait())
        self.root.after(200, self.drain)

    def toggle(self) -> None:
        if self.running:
            self.running = False
            self.btn.configure(text="Start")
            self.status.configure(text="Stopped.")
            self.write("Stopped watching.")
            return

        saves, out = Path(self.saves.get()), Path(self.out.get())
        if not saves.is_dir():
            messagebox.showerror(
                APP,
                "That save folder doesn't exist.\n\n"
                "It's usually:\nDocuments\\Paradox Interactive\\Stellaris\\save games",
            )
            return
        if not self.out.get().strip():
            messagebox.showerror(
                APP,
                "Choose your SillyTavern extension folder.\n\n"
                "Install the extension first, then pick the folder it created:\n"
                "SillyTavern\\data\\default-user\\extensions\\stellaris-advisor",
            )
            return

        save_config({"saves": str(saves), "out": str(out)})
        self.running = True
        self.btn.configure(text="Stop")
        self.status.configure(text="Watching for new saves…")
        self.write(f"Watching {saves}")
        self.write(f"Writing to {out}")
        self.thread = threading.Thread(target=self.loop, args=(saves, out), daemon=True)
        self.thread.start()

    def loop(self, saves: Path, out: Path) -> None:
        last = 0.0
        first = True
        while self.running:
            try:
                p = newest_save(saves)
                if p is None:
                    if first:
                        self.msgs.put("No saves found yet — start a game and it will appear.")
                        first = False
                elif p.stat().st_mtime != last:
                    time.sleep(1.5)  # let the autosave finish writing
                    date = emit(p, out)
                    last = p.stat().st_mtime
                    first = False
                    self.msgs.put(f"Updated briefing — {date} (from {p.name})")
            except Exception as exc:
                self.msgs.put(f"Problem reading that save: {exc}")
            time.sleep(5)

    def close(self) -> None:
        self.running = False
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

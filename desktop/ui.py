import asyncio
import contextlib
import queue
import threading
import tkinter as tk
from collections.abc import Callable, Coroutine
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from tkinter import ttk
from typing import Any

COLORS = {
    "background": "#EEF2F7",
    "surface": "#FFFFFF",
    "ink": "#172B4D",
    "muted": "#52647B",
    "accent": "#176BCA",
    "line": "#D6DFEB",
    "online": "#147D64",
}


def style(root: tk.Tk) -> None:
    root.configure(bg=COLORS["background"])
    theme = ttk.Style(root)
    theme.theme_use("clam")
    theme.configure(
        ".", font=("Segoe UI", 10), background=COLORS["background"], foreground=COLORS["ink"]
    )
    theme.configure("TFrame", background=COLORS["background"])
    theme.configure("TLabel", background=COLORS["background"])
    theme.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground=COLORS["ink"])
    theme.configure("Muted.TLabel", foreground=COLORS["muted"])
    theme.configure("Status.TLabel", font=("Segoe UI Semibold", 11))
    theme.configure("TButton", padding=(12, 8), borderwidth=1)
    theme.configure("Accent.TButton", background=COLORS["accent"], foreground="white")
    theme.map("Accent.TButton", background=[("active", "#1055A2"), ("disabled", "#9AAEC5")])
    theme.configure("TEntry", fieldbackground=COLORS["surface"], padding=6)
    theme.configure(
        "Treeview",
        rowheight=34,
        fieldbackground=COLORS["surface"],
        background=COLORS["surface"],
        borderwidth=0,
    )
    theme.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), padding=8)
    theme.map(
        "Treeview", background=[("selected", "#D8E9FC")], foreground=[("selected", COLORS["ink"])]
    )
    theme.configure("TLabelframe", bordercolor=COLORS["line"])
    theme.configure("TLabelframe.Label", font=("Segoe UI Semibold", 11))


def field(
    parent: tk.Misc, label: str, row: int, variable: tk.StringVar, secret: bool = False
) -> ttk.Entry:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
    entry = ttk.Entry(parent, textvariable=variable, show="●" if secret else "")
    entry.grid(row=row, column=1, sticky="ew", pady=7)
    parent.columnconfigure(1, weight=1)
    return entry


class Bridge:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=4096)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.run, name="kintler-network", daemon=True)
        self.ready = threading.Event()
        self.thread.start()
        self.ready.wait(5)

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def post(self, kind: str, payload: Any) -> bool:
        try:
            self.events.put_nowait((kind, payload))
            return True
        except queue.Full:
            if kind == "output":
                return False
            with contextlib.suppress(queue.Empty):
                self.events.get_nowait()
            self.events.put_nowait((kind, payload))
            return True

    def submit(self, work: Coroutine[Any, Any, Any], success: str = "done") -> Future:
        future = asyncio.run_coroutine_threadsafe(work, self.loop)

        def completed(result: Future) -> None:
            try:
                value = result.result()
            except (asyncio.CancelledError, FutureCancelledError):
                return
            except Exception as exc:
                self.post("error", str(exc))
            else:
                self.post(success, value)

        future.add_done_callback(completed)
        return future

    def drain(self, callback: Callable[[str, Any], None]) -> None:
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            callback(kind, value)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)


def text_output(parent: tk.Misc, height: int = 14) -> tk.Text:
    box = tk.Text(
        parent,
        height=height,
        font=("Consolas", 10),
        bg="#F8FAFD",
        fg=COLORS["ink"],
        relief="flat",
        padx=14,
        pady=12,
        wrap="word",
        state="disabled",
    )
    return box


def append(box: tk.Text, text: str) -> None:
    box.configure(state="normal")
    box.insert("end", text)
    if int(box.index("end-1c").split(".")[0]) > 15000:
        box.delete("1.0", "3000.0")
    count = box.count("1.0", "end-1c", "chars")
    if count and count[0] > 1024 * 1024:
        box.delete("1.0", "1.0 + 524288 chars")
    box.see("end")
    box.configure(state="disabled")


def scroll_form(parent: tk.Misc) -> ttk.Frame:
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True, pady=8)
    canvas = tk.Canvas(container, background=COLORS["background"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    form = ttk.Frame(canvas)
    item = canvas.create_window((0, 0), window=form, anchor="nw")
    form.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))

    def wheel(event: Any) -> None:
        if str(event.widget).startswith(str(form)) or event.widget is canvas:
            canvas.yview_scroll(-int(event.delta / 120), "units")

    parent.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")
    return form

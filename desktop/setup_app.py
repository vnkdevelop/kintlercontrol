import argparse
import asyncio
import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from desktop.deployment import create_deployment
from desktop.ui import Bridge, append, style, text_output


class SetupApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.bridge = Bridge()
        self.url = tk.StringVar(value="https://")
        self.output = tk.StringVar(value=str(Path.home() / "KintlerControl-Deployment"))
        self.mode = tk.StringVar(value="Automatic HTTPS / Автоматический HTTPS")
        self.result: dict | None = None
        root.title("KintlerControl — Setup / Настройка")
        root.geometry("850x620")
        root.minsize(780, 550)
        style(root)
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Your server. Your keys. Your control.", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Свой сервер, свои ключи, подтверждение каждого подключения.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(5, 20))
        for label, variable in (
            ("Relay URL / Адрес сервера", self.url),
            ("Output folder / Каталог комплекта", self.output),
        ):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 3))
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
        ttk.Button(frame, text="Choose folder / Выбрать каталог", command=self.choose).pack(
            anchor="w", pady=8
        )
        ttk.Combobox(
            frame,
            textvariable=self.mode,
            state="readonly",
            values=[
                "Automatic HTTPS / Автоматический HTTPS",
                "Existing HTTPS proxy / Существующий HTTPS-прокси",
            ],
        ).pack(fill="x", pady=8)
        ttk.Label(
            frame,
            text="Automatic mode: DNS → your server, Docker + Compose, TCP 80/443.\nАвтоматический режим: домен указывает на сервер, нужны Docker и TCP 80/443.\nPrivate owner keys stay under Windows DPAPI on this PC; the server kit has public registration only.",
            style="Muted.TLabel",
            wraplength=780,
        ).pack(anchor="w", pady=8)
        self.create = ttk.Button(
            frame,
            text="Create deployment / Создать комплект",
            style="Accent.TButton",
            command=self.generate,
        )
        self.create.pack(anchor="w", pady=10)
        self.log = text_output(frame, 8)
        self.log.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.pump)

    def choose(self) -> None:
        selected = filedialog.askdirectory()
        if selected:
            self.output.set(selected)

    def generate(self) -> None:
        self.create.configure(state="disabled")
        binaries = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1] / "dist/windows"
        )
        mode = "automatic" if self.mode.get().startswith("Automatic") else "existing"
        self.bridge.submit(
            asyncio.to_thread(
                create_deployment, self.url.get(), Path(self.output.get()), binaries, None, mode
            ),
            "created",
        )

    def handle(self, kind: str, value: Any) -> None:
        if kind == "created":
            self.result = value
            append(
                self.log,
                json.dumps(value, ensure_ascii=False, indent=2)
                + "\n\nUpload server-kit.zip → extract → run the command above.\nОтправьте server-kit.zip на сервер, распакуйте и выполните указанную команду.\n",
            )
            self.create.configure(state="normal")
        elif kind == "error":
            self.create.configure(state="normal")
            messagebox.showerror("Setup / Настройка", str(value))

    def pump(self) -> None:
        self.bridge.drain(self.handle)
        self.root.after(50, self.pump)

    def close(self) -> None:
        self.bridge.stop()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create your own KintlerControl deployment without server credentials in public binaries"
    )
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--binaries", type=Path)
    parser.add_argument("--owner-profile", type=Path)
    parser.add_argument("--existing-proxy", action="store_true")
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        root = tk.Tk()
        root.withdraw()
        style(root)
        root.update()
        root.destroy()
        args.self_test.write_text(json.dumps({"ok": True, "role": "setup"}), encoding="utf-8")
        return
    if args.url:
        if not args.output or not args.binaries:
            parser.error("--url requires --output and --binaries")
        print(
            json.dumps(
                create_deployment(
                    args.url,
                    args.output,
                    args.binaries,
                    args.owner_profile,
                    "existing" if args.existing_proxy else "automatic",
                ),
                indent=2,
            )
        )
        return
    root = tk.Tk()
    SetupApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image


def command(argv: list[str], env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)], check=True, env=env
    )


def make_icon(path: Path, source: Path | None = None) -> None:
    source = source or Path(__file__).resolve().parents[1] / "desktop/assets/kintler-controller.png"
    with Image.open(source) as image:
        image.convert("RGBA").save(
            path,
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("controller", "agent"))
    parser.add_argument(
        "--package-only", action="store_true", help="Refresh checksums for existing client EXEs"
    )
    selection = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Windows EXE must be built on Windows")
    root = Path(__file__).resolve().parent.parent
    output = root / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    work = root / "build/windows"
    work.mkdir(parents=True, exist_ok=True)
    icons = {
        "KintlerAgent": work / "kintler-agent.ico",
        "KintlerController": work / "kintler-controller.ico",
    }
    for name, icon in icons.items():
        make_icon(
            icon,
            root
            / "desktop/assets"
            / ("kintler-agent.png" if name == "KintlerAgent" else "kintler-controller.png"),
        )
    entries = [
        ("KintlerAgent", "agent_app.py", False),
        ("KintlerController", "terminal_entry.py", False),
    ]
    try:
        for name, entry, console in entries:
            selected = {
                "controller": "KintlerController",
                "agent": "KintlerAgent",
            }
            if selection.package_only or (selection.only and selected[selection.only] != name):
                continue
            if name == "KintlerAgent":
                cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo.exe")
                environment = dict(os.environ)
                environment.pop("KINTLER_PUBLIC_URL", None)
                environment["KINTLER_ICON_PATH"] = str(icons[name])
                environment["RUSTFLAGS"] = (
                    environment.get("RUSTFLAGS", "") + " -C target-feature=+crt-static"
                )
                command(
                    [
                        cargo,
                        "build",
                        "--release",
                        "--locked",
                        "--manifest-path",
                        str(root / "agent-rust/Cargo.toml"),
                        "--target-dir",
                        str(root / "agent-rust/target"),
                    ],
                    environment,
                )
                shutil.copyfile(
                    root / "agent-rust/target/release/kintler-agent.exe",
                    output / "KintlerAgent.exe",
                )
                continue
            args = [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--onefile",
                "--name",
                name,
                "--distpath",
                str(output),
                "--workpath",
                str(work / name),
                "--specpath",
                str(work),
                "--paths",
                str(root),
                "--icon",
                str(icons[name]),
                "--collect-all",
                "mss",
                "--console" if console else "--windowed",
            ]
            if name == "KintlerController":
                args += [
                    "--collect-data",
                    "mcp",
                    "--copy-metadata",
                    "mcp",
                    "--collect-data",
                    "textual",
                    "--copy-metadata",
                    "textual",
                    "--add-data",
                    f"{output / 'KintlerAgent.exe'};embedded",
                    "--add-data",
                    f"{root / '.agents/skills/kintler-remote'};skills/kintler-remote",
                ]
                for item in (
                    "docker-compose.yml",
                    "docker-compose.tls.yml",
                    "docker-compose.caddy.yml",
                ):
                    args += ["--add-data", f"{root / item};."]
                for item in (
                    "Caddyfile",
                    "bootstrap_backend.py",
                    "start_server.py",
                    "nginx.conf",
                    "existing-nginx.conf",
                ):
                    args += ["--add-data", f"{root / 'deploy' / item};deploy"]
            args += [str(root / "desktop" / entry)]
            command(args)
        manifest = {
            name + ".exe": hashlib.sha256((output / (name + ".exe")).read_bytes()).hexdigest()
            for name, _, _ in entries
        }
        (output / "SHA256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Windows client EXEs and checksums ready: {output}")
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()

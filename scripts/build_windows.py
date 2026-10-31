import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw


def command(argv: list[str], env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)], check=True, env=env
    )


def make_icon(path: Path) -> None:
    image = Image.new("RGBA", (256, 256), "#172B4D")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 28, 228, 228), radius=40, fill="#176BCA")
    draw.polygon(
        [
            (65, 58),
            (89, 58),
            (89, 115),
            (155, 58),
            (190, 58),
            (115, 128),
            (192, 198),
            (155, 198),
            (89, 140),
            (89, 198),
            (65, 198),
        ],
        fill="white",
    )
    image.save(
        path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("controller", "agent", "mcp", "setup"))
    parser.add_argument("--package-only", action="store_true")
    selection = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Windows EXE must be built on Windows")
    root = Path(__file__).resolve().parent.parent
    output = root / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    work = root / "build/windows"
    work.mkdir(parents=True, exist_ok=True)
    icon = work / "kintler.ico"
    make_icon(icon)
    entries = [
        ("KintlerController", "controller_app.py", False),
        ("KintlerAgent", "agent_app.py", False),
        ("KintlerMCP", "mcp_entry.py", True),
        ("KintlerSetup", "setup_app.py", False),
    ]
    try:
        for name, entry, console in entries:
            selected = {
                "controller": "KintlerController",
                "agent": "KintlerAgent",
                "mcp": "KintlerMCP",
                "setup": "KintlerSetup",
            }
            if selection.package_only or (selection.only and selected[selection.only] != name):
                continue
            if name == "KintlerAgent":
                cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo.exe")
                environment = dict(os.environ)
                environment.pop("KINTLER_PUBLIC_URL", None)
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
                str(icon),
                "--collect-all",
                "mss",
                "--console" if console else "--windowed",
            ]
            if console:
                args += ["--collect-data", "mcp", "--copy-metadata", "mcp"]
            if name == "KintlerSetup":
                for item in (
                    "docker-compose.yml",
                    "docker-compose.tls.yml",
                    "docker-compose.caddy.yml",
                ):
                    args += ["--add-data", f"{root / item};."]
                for item in (
                    "Caddyfile",
                    "bootstrap_backend.py",
                    "nginx.conf",
                    "existing-nginx.conf",
                ):
                    args += ["--add-data", f"{root / 'deploy' / item};deploy"]
            args += [str(root / "desktop" / entry)]
            command(args)
        instructions = root / "docs/WINDOWS_EXE.md"
        shutil.copyfile(instructions, output / "ПРОЧИТАТЬ.md")
        manifest = {
            name + ".exe": hashlib.sha256((output / (name + ".exe")).read_bytes()).hexdigest()
            for name, _, _ in entries
        }
        for label, names in [
            (
                "Kintler-Controller-Windows",
                ["KintlerController.exe", "KintlerMCP.exe", "KintlerSetup.exe", "KintlerAgent.exe"],
            ),
            ("Kintler-Agent-Windows", ["KintlerAgent.exe"]),
        ]:
            with zipfile.ZipFile(
                output / (label + ".zip"), "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name in names:
                    archive.write(output / name, name)
                archive.write(instructions, "ПРОЧИТАТЬ.md")
                archive.writestr(
                    "SHA256.json", json.dumps({name: manifest[name] for name in names}, indent=2)
                )
        (output / "SHA256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with zipfile.ZipFile(
            output / "KintlerControl-Windows.zip", "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name in manifest:
                archive.write(output / name, name)
            archive.write(instructions, "START_HERE.md")
            archive.writestr("SHA256.json", json.dumps(manifest, indent=2))
        print(f"Windows release ready: {output}")
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()

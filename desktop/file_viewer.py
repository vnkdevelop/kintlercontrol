import os
import subprocess
import sys
from pathlib import Path


def open_image(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError("Сохранённый снимок не найден.")
    if sys.platform == "win32":
        os.startfile(str(path.resolve()))
    else:
        subprocess.Popen(
            ["open" if sys.platform == "darwin" else "xdg-open", str(path.resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

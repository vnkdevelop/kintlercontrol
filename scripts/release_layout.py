from pathlib import Path

BUNDLE_NAME = "KintlerControl-Bundle.zip"
SKILL_ARCHIVE_NAME = "KintlerControl-Skill.zip"
SKILL_FILES = (
    "SKILL.md",
    "references/mcp-clients.md",
    "references/tools.md",
    "references/desktop.md",
    "references/troubleshooting.md",
    "scripts/install-controller.cmd",
)
SERVER_FILES = {
    "docker-compose.yml": "docker-compose.yml",
    "docker-compose.caddy.yml": "docker-compose.caddy.yml",
    "docker-compose.tls.yml": "docker-compose.tls.yml",
    ".env.example": ".env.example",
    "remote-deploy.env.example": "deploy/.env.example",
    "Caddyfile": "deploy/Caddyfile",
    "bootstrap_backend.py": "deploy/bootstrap_backend.py",
    "start_server.py": "deploy/start_server.py",
    "deploy/nginx.conf": "deploy/nginx.conf",
    "deploy/existing-nginx.conf": "deploy/existing-nginx.conf",
}
DOC_FILES = (
    "DEPLOYMENT.md",
    "TERMINAL.md",
    "WINDOWS_EXE.md",
    "SINGLE_EXE.md",
    "PROJECT_LAYOUT.md",
    "AI_SETUP.md",
    "SERVER_START.md",
)


def bundle_sources(root: Path, directory: Path) -> dict[str, Path]:
    clients = list((directory / "windows").rglob("*.exe"))
    if len(clients) != 2 or {path.name for path in clients} != {
        "KintlerAgent.exe",
        "KintlerController.exe",
    }:
        raise ValueError("Release requires exactly two generic self-contained client EXEs")
    checksums = list((directory / "windows").rglob("SHA256.json"))
    if len(checksums) != 1:
        raise ValueError("Client SHA256.json is missing or ambiguous")
    files = {
        "START_HERE.md": root / "docs/START_HERE.md",
        "LICENSE": root / "LICENSE",
        "THIRD_PARTY_NOTICES.md": root / "docs/THIRD_PARTY_NOTICES.md",
        "licenses/agent-desktop.txt": root / "docs/AGENT_DESKTOP_LICENSE.txt",
        "clients/windows/SHA256.json": checksums[0],
        "server/kintlercontrol-image.tar": directory / "backend/kintlercontrol-image.tar",
        "server/kintlercontrol-image.tar.sha256": directory
        / "backend/kintlercontrol-image.tar.sha256",
    }
    files.update({"clients/windows/" + path.name: path for path in clients})
    files.update({"server/" + name: root / source for name, source in SERVER_FILES.items()})
    files.update({"docs/" + name: root / "docs" / name for name in DOC_FILES})
    files.update(
        {
            "skills/kintler-remote/" + name: root / ".agents/skills/kintler-remote" / name
            for name in SKILL_FILES
        }
    )
    if any(not path.is_file() for path in files.values()):
        raise ValueError("A required generic bundle file is missing")
    return files

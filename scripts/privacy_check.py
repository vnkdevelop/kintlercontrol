import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_NAMES = {
    ".env",
    "owner-public.json",
    "kintler-agent.json",
    "server-config.json",
    "deployment.json",
    "publication-policy.json",
    "publication-report.json",
    "installation.json",
    "local-installation.json",
}
FORBIDDEN_PARTS = {
    ".venv",
    "dist",
    "build",
    "target",
    "graphify-out",
    "profiles",
    "deployments",
    ".private",
    "private",
    "certificates",
    "backups",
    "logs",
    "__pycache__",
}
FORBIDDEN_SUFFIXES = {
    ".exe",
    ".dll",
    ".pyd",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".sqlite3",
    ".db",
    ".zip",
    ".tar",
    ".pyc",
    ".log",
}
PATTERNS = [
    ("private key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "embedded credential",
        re.compile(
            r"[\"'](?:secret|password|private_key|token)[\"']\s*:\s*[\"'][A-Za-z0-9_+/=-]{24,}[\"']",
            re.IGNORECASE,
        ),
    ),
    ("embedded device identity", re.compile(r"node_[0-9a-f]{16}\b", re.IGNORECASE)),
    (
        "embedded public registration",
        re.compile(r"[\"']public_key[\"']\s*:\s*[\"'][A-Za-z0-9+/]{43}=[\"']"),
    ),
]


def run_git(root: Path, args: list[str]) -> bytes:
    argv = ["git", "-C", str(root), *args]
    if sys.platform == "win32":
        argv = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
    return subprocess.check_output(argv)


def inspect_text(name: str, data: bytes, denied: list[str]) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    findings = [f"{name}: {reason}" for reason, pattern in PATTERNS if pattern.search(text)]
    if any(value and value.casefold() in text.casefold() for value in denied):
        findings.append(f"{name}: deployment-specific information")
    return findings


def scan(root: Path, denied: list[str], staged: bool = False) -> tuple[int, list[str]]:
    names = (
        run_git(
            root,
            ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
            if staged
            else ["ls-files", "-z"],
        )
        .decode()
        .split("\0")
    )
    findings = []
    count = 0
    for name in filter(None, names):
        count += 1
        path = Path(name)
        if (
            path.name in FORBIDDEN_NAMES
            or any(part in FORBIDDEN_PARTS for part in path.parts)
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or (path.name.startswith(".env") and path.name != ".env.example")
        ):
            findings.append(f"{name}: private/generated file must not be tracked")
            continue
        data = run_git(root, ["show", f":{name}"]) if staged else (root / name).read_bytes()
        findings.extend(inspect_text(name, data, denied))
    return count, findings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail closed if tracked sources contain private/generated files or deployment identities"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged", action="store_true")
    parser.add_argument(
        "--deny-file",
        type=Path,
        help="Local-only JSON list of deployment-specific strings; never commit this policy",
    )
    args = parser.parse_args()
    denied = json.loads(args.deny_file.read_text()) if args.deny_file else []
    count, findings = scan(args.root, denied, args.staged)
    if findings:
        print("Publication blocked:\n" + "\n".join(findings))
        raise SystemExit(1)
    print(
        f"Privacy check passed: {count} tracked source files; no credentials or deployment identities detected."
    )


if __name__ == "__main__":
    main()

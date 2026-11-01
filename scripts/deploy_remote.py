import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from dotenv import dotenv_values


def command(
    arguments: list[str], *, data: str | None = None, timeout: int = 180
) -> subprocess.CompletedProcess:
    if os.name == "nt":
        arguments = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(arguments)]
    try:
        return subprocess.run(
            arguments, input=data, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Deployment command timed out; inspect only this deployment before retrying"
        ) from None


def settings(path: Path) -> dict[str, str]:
    if path.name != ".env":
        raise ValueError("SSH credentials must be read from an ignored local .env")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    for name in (
        "KINTLER_SSH_HOST",
        "KINTLER_SSH_USER",
        "KINTLER_SSH_KEY_PATH",
        "KINTLER_SERVER_FOLDER",
    ):
        if not values.get(name):
            raise ValueError(f"Missing {name} in the local deployment .env")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", values["KINTLER_SSH_USER"]):
        raise ValueError("Invalid SSH username")
    host = values["KINTLER_SSH_HOST"]
    try:
        ipaddress.ip_address(host)
        host_valid = True
    except ValueError:
        host_valid = bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host))
    if not host_valid:
        raise ValueError("Invalid SSH host")
    values["KINTLER_SSH_KEY_PATH"] = str(Path(values["KINTLER_SSH_KEY_PATH"]).expanduser())
    if not Path(values["KINTLER_SSH_KEY_PATH"]).is_file():
        raise ValueError("SSH key file is missing; no password is accepted on the command line")
    if any(character in values["KINTLER_SSH_KEY_PATH"] for character in '&|<>^"\r\n'):
        raise ValueError("SSH key path contains unsupported command-line characters")
    directory = values.get("KINTLER_REMOTE_DIRECTORY", "/opt/kintlercontrol")
    if not re.fullmatch(r"/opt/[A-Za-z0-9_.-]+", directory):
        raise ValueError("Remote deployment must be a dedicated directory directly under /opt")
    project = values.get("KINTLER_COMPOSE_PROJECT", "kintlercontrol")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", project):
        raise ValueError("Invalid dedicated Compose project name")
    values["KINTLER_REMOTE_DIRECTORY"], values["KINTLER_COMPOSE_PROJECT"] = directory, project
    return values


def deploy(path: Path, image: str | None = None, dry_run: bool = False) -> dict:
    values = settings(path)
    folder = Path(values["KINTLER_SERVER_FOLDER"]).expanduser().resolve()
    if not (folder / ".env").is_file() or not (folder / "docker-compose.yml").is_file():
        raise ValueError(
            "KINTLER_SERVER_FOLDER must be the generated server folder, not a directory with client profiles"
        )
    image = image or values.get("KINTLER_DEPLOY_IMAGE", "ghcr.io/vnkdevelop/kintlercontrol:1.3.0")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", image):
        raise ValueError("Invalid Docker image reference")
    target = values["KINTLER_SSH_USER"] + "@" + values["KINTLER_SSH_HOST"]
    ssh_options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=15",
        "-i",
        values["KINTLER_SSH_KEY_PATH"],
    ]
    port = values.get("KINTLER_SSH_PORT", "22")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Invalid SSH port")
    with tempfile.TemporaryDirectory(prefix="kintler-deploy-") as temporary:
        archive = Path(temporary) / "deployment.zip"
        names = [
            ".env",
            "docker-compose.yml",
            "docker-compose.caddy.yml",
            "docker-compose.tls.yml",
            "Caddyfile",
            "owner-public.json",
        ]
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name in names:
                if (folder / name).is_file():
                    package.write(folder / name, name)
            for name in ("nginx.conf", "existing-nginx.conf"):
                if (folder / "deploy" / name).is_file():
                    package.write(folder / "deploy" / name, "deploy/" + name)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        config = {
            "directory": values["KINTLER_REMOTE_DIRECTORY"],
            "project": values["KINTLER_COMPOSE_PROJECT"],
            "image": image,
            "package": base64.b64encode(archive.read_bytes()).decode("ascii"),
            "checksum": checksum,
            "dry_run": dry_run,
            "caddy": (folder / "Caddyfile").is_file()
            and (folder / "docker-compose.caddy.yml").is_file(),
        }
        script = "CFG=" + repr(config) + "\n" + REMOTE
        result = command(
            ["ssh", *ssh_options, "-p", port, target, "python3", "-"], data=script, timeout=360
        )
        if result.returncode:
            raise RuntimeError(
                "Deployment failed or rolled back. SSH target and credentials are intentionally omitted from logs"
            )
        return json.loads(result.stdout)


REMOTE = r"""
import base64,hashlib,io,json,os,pathlib,shutil,subprocess,tempfile,time,zipfile
ROOT=pathlib.Path(CFG['directory'])
def run(args,check=True,timeout=60):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and p.returncode:raise RuntimeError('Operation failed: '+args[0])
    return p
def compose(*args,check=True):return run(['docker','compose','--project-name',CFG['project'],'--project-directory',str(ROOT),'-f',str(ROOT/'docker-compose.yml'),*(['-f',str(ROOT/'docker-compose.caddy.yml')] if CFG['caddy'] else []),*args],check,180)
def environment(file):
    result={}
    for line in file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key,value=line.split('=',1);result[key]=value.strip().strip("'\"")
    return result
def services():
    p=run(['systemctl','list-units','--state=running','--type=service','--no-legend','--no-pager'])
    result={}
    for line in p.stdout.splitlines():
        name=line.split()[0]
        if name in {'user@0.service'}:continue
        data=run(['systemctl','show',name,'--property=ActiveState,MainPID','--no-pager']).stdout
        result[name]=data
    return result
before=services()
containers=[]
for line in run(['docker','ps','--format','{{.ID}}']).stdout.splitlines():
    details=json.loads(run(['docker','inspect',line]).stdout)[0]
    if details.get('Config',{}).get('Labels',{}).get('com.docker.compose.project')!=CFG['project']:containers.append((line,details['State']['Status'],details['State']['StartedAt']))
run(['docker','compose','version'])
payload=base64.b64decode(CFG['package'],validate=True)
if hashlib.sha256(payload).hexdigest()!=CFG['checksum']:raise RuntimeError('Upload checksum mismatch')
def preflight(stage):
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        for name in package.namelist():
            path=pathlib.PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:raise RuntimeError('Invalid archive path')
        package.extractall(stage)
    previous=environment(ROOT/'.env') if (ROOT/'.env').is_file() else None
    incoming=environment(stage/'.env')
    if not incoming.get('KINTLER_CREDENTIALS'):raise RuntimeError('Local server folder is not a configured Kintler deployment')
    if ROOT.exists() and any(ROOT.iterdir()) and (not previous or not previous.get('KINTLER_CREDENTIALS')):raise RuntimeError('Remote directory is not a recognized Kintler deployment')
    if previous and previous.get('KINTLER_CREDENTIALS')!=incoming.get('KINTLER_CREDENTIALS'):raise RuntimeError('Owner registration differs; refusing to replace credentials')
    if ROOT.exists() and any(ROOT.iterdir()) and not (ROOT/'docker-compose.yml').is_file():raise RuntimeError('Remote directory is not this deployment')
    existing=run(['docker','ps','-a','--filter','label=com.docker.compose.project='+CFG['project'],'--format','{{.ID}}']).stdout.splitlines()
    for identity in existing:
        labels=json.loads(run(['docker','inspect',identity]).stdout)[0]['Config'].get('Labels',{})
        if labels.get('com.docker.compose.project.working_dir')!=str(ROOT):raise RuntimeError('Compose project belongs to a different directory')
    if CFG['dry_run']:
        print(json.dumps({'preflight':True,'docker_ready':True,'compose_ready':True,'existing_deployment':previous is not None,'owner_registration_verified':True,'unrelated_containers':len(containers)}))
    else:
        update(stage,previous,existing)

def update(stage,previous,existing):
    backup=None
    changed=False
    created=[]
    if previous:CFG['caddy']=(ROOT/'Caddyfile').is_file() and (ROOT/'docker-compose.caddy.yml').is_file()
    try:
        ROOT.mkdir(mode=0o700,exist_ok=True)
        if previous:
            backup=ROOT/'.deploy-backups'/str(time.time_ns());backup.mkdir(parents=True)
            os.chmod(backup,0o700)
            for path in stage.rglob('*'):
                relative=path.relative_to(stage)
                current=ROOT/relative
                if path.is_file() and current.is_file():
                    saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(current,saved)
        run(['docker','pull',CFG['image']],timeout=180)
        changed=True
        for path in stage.rglob('*'):
            if path.is_file():
                if previous and path.name=='.env':continue
                destination=ROOT/path.relative_to(stage)
                if not destination.exists():created.append(destination)
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
        lines=(ROOT/'.env').read_text().splitlines()
        lines=[line for line in lines if not line.startswith('KINTLER_IMAGE=')]
        lines.append("KINTLER_IMAGE='"+CFG['image']+"'")
        (ROOT/'.env').write_text('\n'.join(lines)+'\n');os.chmod(ROOT/'.env',0o600)
        compose('config','--quiet');compose('up','-d','--no-build')
        container=compose('ps','-q','relay').stdout.strip()
        for _ in range(45):
            info=json.loads(run(['docker','inspect',container]).stdout)[0]
            if info['State'].get('Health',{}).get('Status')=='healthy':break
            time.sleep(2)
        else:raise RuntimeError('Container did not become healthy')
        if services()!=before:raise RuntimeError('Existing system service state or PID changed')
        for identity,status,started in containers:
            current=json.loads(run(['docker','inspect',identity]).stdout)[0]['State']
            if current['Status']!=status or current['StartedAt']!=started:raise RuntimeError('Unrelated container changed')
        print(json.dumps({'deployed':True,'healthy':True,'unrelated_services_unchanged':True,'unrelated_containers_unchanged':True,'restart_count':info['RestartCount'],'backup_created':backup is not None}))
    except Exception:
        if changed:
            if not backup and not existing:compose('down',check=False)
            for path in created:path.unlink(missing_ok=True)
            if backup:
                for path in backup.rglob('*'):
                    if path.is_file():shutil.copy2(path,ROOT/path.relative_to(backup))
                compose('up','-d','--no-build',check=False)
        raise
with tempfile.TemporaryDirectory(prefix=CFG['project']+'-stage-',dir='/tmp') as temporary:
    preflight(pathlib.Path(temporary))
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy only a dedicated KintlerControl Compose project using an ignored SSH .env"
    )
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--image")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = deploy(args.env, args.image, args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

<div align="center">

# KintlerControl

**Your server. Your devices. Your approval.**

A self-hosted remote-management system with a lightweight native Windows agent,
a desktop controller, an MCP bridge, and a Docker relay.

[![CI](https://github.com/vnkdevelop/kintlercontrol/actions/workflows/ci.yml/badge.svg)](https://github.com/vnkdevelop/kintlercontrol/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/clients-Windows%2010%2F11-0078D4.svg)](#requirements)
[![Self hosted](https://img.shields.io/badge/backend-self--hosted-147D64.svg)](#quick-start)

[English](README.md) · [Русский](README.ru.md) · [Releases](https://github.com/vnkdevelop/kintlercontrol/releases) · [Security](SECURITY.md)

</div>

---

## What it does

KintlerControl connects your Windows devices through **your own relay server**.
Both clients make outbound HTTPS/WebSocket connections; the target PC does not
need an exposed SSH port or inbound port forwarding.

**Every new agent connection requires an explicit owner decision.** Restarting
the agent, reconnecting after a network interruption, or renewing an expired
session creates a new approval request—even for a known device. The server
remembers identities, **not permanent access grants**.

| Component | Purpose |
|---|---|
| `KintlerAgent.exe` | Native Rust / Win32 agent, no Python or WebView installation |
| `KintlerController.exe` | Complete desktop app: inventory, approval, commands, setup, MCP and deployment modes |
| Docker relay | Authentication, routing, inventory and audit on your infrastructure |

### Tools

- Streaming `cmd` / PowerShell commands with deadlines and cancellation.
- Chunked upload/download with SHA-256 verification and overwrite protection.
- CPU, RAM, disks, network information and GPU inventory.
- Process inventory and PID-reuse checks before termination.
- PNG screenshots of an interactive Windows desktop.
- Windows registry, services and scheduled tasks, gated by actual OS privileges.
- 12 MCP tools for permitted-device management from your AI client.

Use this software only on devices you own or are explicitly authorized to manage.

## Quick start

### 1. Prepare your server

Have Docker Engine + Compose installed and point a DNS hostname, such as
`relay.example.com`, to your server. For automatic HTTPS, ports **80 and 443**
must be free and reachable. No particular hosting provider or country is required.

### 2. Download the Windows kit

Get **`KintlerControl-Windows.zip`** from [Releases](https://github.com/vnkdevelop/kintlercontrol/releases/latest).
Extract it on your **controller PC**. The controller itself is one self-contained EXE and can be moved anywhere.

Run **`KintlerController.exe`** and use **`/setup HTTPS_URL OUTPUT`**, enter your HTTPS origin, choose an empty output folder,
and select **Automatic HTTPS**. The wizard creates:

```text
Your-Deployment/
├── server-kit.zip       # upload this to your server
├── server/              # Compose, public owner registration and instructions
└── Agent/
    └── KintlerAgent.exe  # configured single executable, no JSON sidecar
```

Your private owner key stays on your PC under Windows DPAPI.
The server kit contains **only the public owner registration**.

### 3. Start Docker

Upload `server-kit.zip` by SFTP/SCP, extract it into a private application directory,
and run the command written in its `START_HERE.md`:

```bash
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d
```

Caddy obtains and renews the HTTPS certificate automatically. The relay stays
behind the proxy; its direct host port is loopback-only.

The complete Windows release also includes a portable backend image. When present,
the wizard adds it to the server kit and writes a `docker load` command before
Compose—no registry login or local image build is required.

Already have Nginx or another HTTPS proxy? Choose **Existing HTTPS proxy** in the
wizard and follow [the deployment guide](docs/DEPLOYMENT.md). Do not replace a
working web-server configuration or start a second listener on an occupied port.

### 4. Connect a device

Open **`KintlerController.exe`** on the owner PC. Copy only the generated **KintlerAgent.exe**
to any folder on the target Windows PC and open it. Neither client needs adjacent JSON/CMD/helper EXE files.

Use `/requests` and `/approve <number> <six-digit code>` in the Controller terminal,
using the code displayed by that Agent, including known devices. Then select its short
name with `/use` and use `/help` for commands and descriptions.

> A known key still needs a fresh code and new approval for every connection. There is no
> “always allow” switch. A refusal or expired request does not grant future access.

## Security model

- TLS **1.3** with certificate validation; no insecure Internet mode.
- New clients use individual **Ed25519** identities; private keys are not embedded
  in public EXE files, repositories, or server kits.
- Windows DPAPI protects local private keys for their original user context.
- The owner is registered explicitly on the server; unknown clients cannot become owners.
- Approval is bound to one live socket/session and cannot authorize a later reconnect.
- Node/tool ACLs, local agent policy, OS privilege checks and structured audit.
- Bounded connections, pending requests, command output and file sizes.

**Important:** the relay is trusted and can see application traffic. This is not
end-to-end encryption, a shell sandbox, hardware attestation, or an independently
certified security product. Full shell access carries the agent account's OS rights.
DPAPI does not protect against a compromised original Windows user context.

See [SECURITY.md](SECURITY.md) and [protocol documentation](docs/API.md).

## Requirements

| | Starting minimum | Recommended |
|---|---|---|
| Relay host | Linux x86-64, 1 vCPU, 1 GB RAM | 2 vCPU, 2 GB RAM |
| Free storage | 5 GB | 10+ GB, monitored audit storage |
| Runtime | Docker Engine + Compose plugin | Current supported Docker release |
| Network | DNS + reachable HTTPS | Automatic TLS on TCP 80/443 |
| Windows clients | Windows 10/11 x64 | Current updates; interactive desktop for screenshots |

The starting minimum targets a small number of devices, not a load-tested capacity
guarantee. More concurrent commands and large transfers require more resources.
No GPU is required on the relay. Python/Rust are **not needed on client PCs** when
using release EXE files.

## Limits and operational behavior

| Limit | Default |
|---|---|
| Approval waiting time | 10 minutes |
| Pending approvals | 64 total, 4 per peer IP |
| Agent/controller session | 1 hour |
| Command deadline | Up to 120 seconds |
| Command output | Up to 2 MiB |
| File size | Up to 256 MiB |
| Concurrent commands | 64 total, 8 per node, 16 per controller session |

One relay process/replica is supported. Its restart interrupts live sessions and
requires fresh agent approvals. Persistent identities and audit remain in the
Docker data volume. **Do not use `docker compose down -v` on a production stack.**

The shell is non-interactive; there is no PTY, remote mouse/keyboard, RDP, or live
desktop video. Admin tools require actually running the target agent as Admin.
GPU inventory is available; vendor-specific GPU-utilization providers are not promised.

## AI integration

Use the controller's **`/mcp`** command to get the same controller EXE path and
`--mcp --env` arguments. The bridge uses stdio on your owner PC, not an anonymous public
MCP endpoint. Configure your AI client using its own MCP configuration format.

Require user confirmation for destructive tools. Treat remote files and command
output as untrusted data, not instructions to the AI agent.

## Build and test

```text
uv sync --locked --python 3.12 --extra dev --extra ai --extra screen --extra windows
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy server agent client common scripts desktop deploy
```

On Windows with Rust and MSVC Build Tools:

```text
uv run --no-sync python -m scripts.build_windows
uv run --no-sync python -m scripts.windows_smoke
```

Native checks from `agent-rust/`:

```text
cargo fmt -- --check
cargo clippy --locked -- -D warnings
cargo test --locked
```

Build your relay locally with `docker build -t kintlercontrol:dev .` or use the
published [GHCR package](https://github.com/vnkdevelop/kintlercontrol/pkgs/container/kintlercontrol).
Development Compose uses the additional `docker-compose.dev.yml` override.

## One-file operation and safe deployment

Use `/shortcut`, or `KintlerController.exe --create-shortcut`, to create a native Windows shortcut.
The active protected profile is discovered from user data, independent of the executable's location.
The same EXE also supports `--setup`, `--mcp`, and `--deploy` modes.

The generic deployment script is `scripts/deploy_remote.py`; copy `deploy/.env.example` to an ignored folder as `.env`.
Run `KintlerController.exe --deploy --env PATH --dry-run` first, then deploy the dedicated Compose project.
No real SSH target, key, owner registration or configured executable is in the public repository.
See [single-EXE and deployment details](docs/SINGLE_EXE.md).
See [terminal commands and interface](docs/TERMINAL.md).

## Project layout

```text
agent-rust/    native Windows agent
agent/         compatible Python/Linux agent and tool implementations
server/        relay, authentication, approvals, routing, SQLite audit
client/        CLI, async SDK and MCP tools
desktop/       controller and deployment wizard
deploy/        generic proxy and bootstrap templates
tests/         protocol, isolation, approval and setup regression tests
scripts/       current build, validation, release and deployment commands
.agents/skills/ reusable agent instructions, including Kintler MCP setup
dist/windows/ current generic client EXEs and SHA-256 manifest
dist/releases/ versioned public release archives
.private/      ignored personal clients, deployment settings and current reports
```

See [project layout and client locations](docs/PROJECT_LAYOUT.md). Personal configured clients
are kept in `.private/clients/windows/`; do not publish or confuse them with generic binaries.

Generated deployments, `.env` files, identities, databases, logs, build outputs,
EXE files and graph caches are excluded from Git. Release binaries are built from
the clean public source—not from a developer's personal configured installation.

## License

[MIT](LICENSE). No hosted service, third-party server access, or commercial support
is bundled. Public binaries are currently unsigned; SmartScreen may warn. Do not
disable security software to install them; verify release checksums and use a
trusted download source.

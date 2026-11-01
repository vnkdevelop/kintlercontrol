# Project layout and client locations

## Ready-to-use personal clients

For a locally configured installation, copy `.private/clients/windows/KintlerAgent.exe`
to the Windows PC being controlled. This EXE already contains that installation's public
HTTPS endpoint. It does not contain private keys and needs no adjacent JSON/CMD/helper EXE.
Start it and enter its fresh six-digit code in the owner Controller for each connection.

`.private/clients/windows/KintlerController.exe` is a portable copy of the Controller.
The installed Controller normally lives at
`%LOCALAPPDATA%\Programs\KintlerControl\KintlerController.exe`; use its Desktop shortcut.
Moving the EXE on the same PC does not move its private identity: owner and device profiles
remain under `%LOCALAPPDATA%\KintlerControl`, protected by DPAPI.

Personal clients and configuration are ignored by Git. Never upload them as public assets.

## Source and development

| Directory | Purpose |
| --- | --- |
| `agent-rust/` | Native Windows agent and its tools. |
| `agent/` | Compatible Python/Linux agent and tool implementations. |
| `server/` | Relay, authentication, per-connection approval, routing and audit. |
| `client/` | SDK, CLI and stdio MCP API. |
| `desktop/` | Desktop Controller, Setup and local protected-profile integration. |
| `common/` | Shared identity and protocol code. |
| `deploy/` | Universal Docker/proxy/bootstrap templates, not personal configuration. |
| `scripts/` | Current build, test, privacy, release and deployment commands. |
| `tests/` | Required regression tests; CI runs them before publishing. |
| `.agents/skills/` | Reusable AI instructions and client-specific MCP setup guidance. |
| `.github/` | CI and release workflows. |
| `.venv/` | Local development dependencies; recreated through `uv sync`. |
| `graphify-out/` | Ignored knowledge graph used by project agents. |

Root Docker Compose files, `Dockerfile`, `pyproject.toml`, lockfiles, security documentation,
agent rules and Git ignore files are required. Source directories stay in place so imports,
tests, packaging and public installations keep working.

## Generated and local-only files

```text
dist/
  windows/                         generic EXEs + SHA256.json, used by build/smoke/CI
  releases/v1.4.0/                 current public Windows/backend ZIPs + SHA256SUMS.txt
.private/
  clients/windows/                 configured personal Agent + portable Controller
  deployment/server/               actual server kit: .env, Compose, public registration, proxy files
  deployment/DeployServer.cmd       convenient wrapper for the installed Controller --deploy mode
  ssh/.env                         ignored SSH and deployment settings
  reports/                         current functional, safety and cleanup reports
```

`dist/windows/KintlerAgent.exe` is generic: configure it through Controller Setup before use.
It is not the personal ready-to-copy Agent. Public distributions are assembled with
`scripts.assemble_release`; the Windows build no longer creates three duplicate client ZIPs.
Other release versions may be kept explicitly when needed for rollback; obsolete local
artifacts can be removed without changing published GitHub releases.

## Safe personal deployment

SSH settings are read from `.private/ssh/.env`, with `KINTLER_SERVER_FOLDER` pointing to
`.private/deployment/server/`. No live credentials belong in this document.

From the project root, run the local wrapper for a read-only preflight:

```bat
cmd.exe /c .private\deployment\DeployServer.cmd --dry-run
```

The wrapper is optional. The installed Controller also accepts `--deploy --env PATH`
directly. It updates only the configured dedicated Compose project, preserving data and
checking unrelated services. Local file cleanup is not a production deployment and does
not delete remote data volumes or server-side rollback backups.

## Русский: что копировать

- **На управляемый ПК:** только `.private\clients\windows\KintlerAgent.exe`.
- **Для управления:** ярлык KintlerControl или установленный Controller; MCP человеку не нужен.
- **Для AI:** скилл `.agents\skills\kintler-remote\SKILL.md` настраивает MCP текущей среды.
- **Для своего сервера:** `.private\deployment\server\` и настройки `.private\ssh\.env`.
- **Для разработки:** исходники, `tests`, `scripts`, шаблоны, lockfiles и `.venv` сохраняются.
- **Для публичной раздачи:** только универсальные файлы из `dist`, не `.private`.

# One executable per client

`KintlerController.exe` is the complete desktop application. It contains the setup wizard,
MCP bridge and deployment CLI as modes of the same file. No adjacent Setup/MCP executable,
JSON or CMD launcher is required for controller operation.

- Normal launch: desktop controller.
- `--setup`: create your deployment and a configured single-file agent.
- `--mcp`: stdio MCP server for an AI client using this same EXE.
- `--create-shortcut`: create a native Windows desktop shortcut.
- `--activate-profile PATH`: select a local protected owner profile inside KintlerControl user data.
- `--deploy --env PATH --dry-run`: inspect the server using only local SSH settings.
- `--deploy --env PATH --image IMAGE`: deploy only the dedicated Compose project.

The executable can be placed anywhere. Private identities remain outside the executable,
protected by Windows DPAPI. The active-profile pointer contains only a local file path.
It is never part of a public release. Moving an EXE to another PC does not move its private key.

The configured `KintlerAgent.exe` also works alone. Setup adds only the public HTTPS origin
to its executable overlay; no key, password or JSON sidecar is needed. A generic public
agent has no endpoint and must first be configured through the controller's Setup mode.
Each new connection needs its fresh six-digit Agent code entered by the owner in Controller,
including known devices. Executable personalization currently
targets unsigned EXE files; sign a configured binary afterwards if publisher signing is required.

## Deployment safety

Copy `deploy/.env.example` into an ignored directory as `.env`, fill SSH host/user/key path,
the generated server folder, dedicated remote directory and Compose project name.
The deployer checks existing services/containers, refuses different owner credentials,
backs up its own configuration, preserves data volumes and rolls back on failure.
It never rewrites unrelated Nginx configuration or restarts Docker/system services.

## AI connection

Manual desktop control does not use MCP. The project skill at
`.agents/skills/kintler-remote/SKILL.md` lets an AI set up its local MCP connection on request.
It selects the current client's native MCP configuration instead of assuming OpenCode;
client-specific JSON/TOML and CLI guidance is in the skill's `references/mcp-clients.md`.
An existing Controller is reused, including portable locations; installation is only needed when absent.
Its Windows installer downloads the official Controller, checks the published SHA-256,
preserves existing EXEs and owner profiles, and cleans temporary files. Installing MCP does
not issue an owner identity or authorize any device. The user still starts Agent, reads its
code and enters it in Controller; there is no additional confirmation button on Agent.

## Русский

Контроллер переносится одним EXE. Мастер, MCP и CLI деплоя находятся внутри него;
для обычного запуска CMD/JSON/вспомогательные EXE не нужны. Ключ хранится в защищённом
локальном профиле, а не в программе. Ярлык создаётся нативным Windows ShellLink API.

Настроенный агент также переносится одним EXE: внутри только публичный адрес, не секреты.
При первом запуске он создаёт собственный ключ под DPAPI; каждое соединение требует
нового кода из окна Agent и подтверждения владельца в Controller, даже для знакомого ключа.
На ПК с Agent дополнительных кнопок не нужно. Публичные бинарники не привязаны к чьему-либо серверу.

SSH-реквизиты деплоя заполняются только в локальном игнорируемом `.env`. Сначала используйте
`--dry-run`; затем деплой обновляет только указанный Compose-проект с backup/rollback.
Чужие контейнеры, службы и Nginx не останавливаются и не переписываются.

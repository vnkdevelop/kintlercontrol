# Windows release / Windows-комплект

Download `KintlerControl-Windows.zip`, extract it on your owner PC, and run
`KintlerController.exe`. Its command-first terminal uses `/help` and `/setup HTTPS_URL OUTPUT`
for initial configuration. The release contains no server address, owner identity or device keys.

- `KintlerController.exe`: black/forest-green terminal app with descriptions, completion, automatic saved-profile login and bottom-right connection notifications; Setup, MCP, shortcut and deployment modes stay in the same EXE.
- `KintlerAgent.exe`: native Rust agent; distribute only the configured EXE to target devices. No JSON sidecar is required.

Private owner data: `%LOCALAPPDATA%\KintlerControl\controller\.env`.
Agent identities: `%LOCALAPPDATA%\KintlerControl\agents\<realm>\.env`.
These paths are separate from any earlier personal installation and are not in release archives. The active owner profile is discovered from local user data, so the controller EXE can move to any folder without moving private keys.

Agent displays a large six-digit code, then operator/AI activity. For screenshots, use an interactive Windows desktop.
Read-only service/task/registry inspection obeys OS ACL. For administrative changes,
explicitly run the agent with Administrator rights. There is no hidden installation,
automatic service setup, automatic elevation, or anonymous access.

## Русский

Распакуйте Windows-комплект на ПК владельца. Controller — терминал с `/help`, `/devices`, `/use` и `/approve`.
При готовом профиле подключается автоматически, ежедневной формы входа нет. Для нового своего сервера — `/setup`.
MCP и деплой остаются режимами того же EXE. На целевой ПК переносите **только настроенный Agent.exe**.
Он показывает крупный 6-значный код: введите его через `/approve` в Controller, затем выберите короткое имя ПК.
Знакомое устройство тоже запрашивает новое разрешение на каждое соединение.
Для изменений служб/реестра/задач нужен явный запуск от администратора; автоповышения нет.

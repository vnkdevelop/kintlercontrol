# Windows release / Windows-комплект

Download `KintlerControl-Windows.zip`, extract it on your owner PC, and run
`KintlerController.exe`, then use its Setup mode. The release contains no server address, owner identity or
device keys. The wizard creates them for your own deployment.

- `KintlerController.exe`: the complete desktop app with Setup, MCP, shortcut and deployment modes inside the same executable.
- `KintlerAgent.exe`: native Rust agent; distribute only the configured EXE to target devices. No JSON sidecar is required.

Private owner data: `%LOCALAPPDATA%\KintlerControl\controller\.env`.
Agent identities: `%LOCALAPPDATA%\KintlerControl\agents\<realm>\.env`.
These paths are separate from any earlier personal installation and are not in release archives. The active owner profile is discovered from local user data, so the controller EXE can move to any folder without moving private keys.

For screenshots, use an interactive Windows desktop. For administrative tools,
explicitly run the agent with Administrator rights. There is no hidden installation,
automatic service setup, automatic elevation, or anonymous access.

## Русский

Распакуйте Windows-комплект на ПК владельца и запустите мастер. Он попросит
только адрес своего HTTPS-сервера и каталог вывода, создаст защищённый профиль
владельца и готовый комплект для Docker. Мастер, MCP и деплой — режимы того же Controller.exe. На целевые ПК переносите **только настроенный Agent.exe**. После запуска подтвердите запрос в контроллере.
Знакомое устройство тоже запрашивает новое разрешение на каждое соединение.
Для Admin-инструментов нужен явный запуск от администратора; автоповышения нет.

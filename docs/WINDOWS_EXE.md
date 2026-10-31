# Windows release / Windows-комплект

Download `KintlerControl-Windows.zip`, extract it on your owner PC, and run
`KintlerSetup.exe`. The release contains no server address, owner identity or
device keys. The wizard creates them for your own deployment.

- `KintlerSetup.exe`: HTTPS address + output folder → server kit and configured Agent folder.
- `KintlerController.exe`: owner desktop controller; uses the locally generated DPAPI profile.
- `KintlerAgent.exe`: native Rust agent; distribute the generated EXE + JSON folder to target devices.
- `KintlerMCP.exe`: launched by your MCP-compatible AI client, using the controller profile.

Private owner data: `%LOCALAPPDATA%\KintlerControl\controller\.env`.
Agent identities: `%LOCALAPPDATA%\KintlerControl\agents\<realm>\.env`.
These paths are separate from any earlier personal installation and are not in release archives.

For screenshots, use an interactive Windows desktop. For administrative tools,
explicitly run the agent with Administrator rights. There is no hidden installation,
automatic service setup, automatic elevation, or anonymous access.

## Русский

Распакуйте Windows-комплект на ПК владельца и запустите мастер. Он попросит
только адрес своего HTTPS-сервера и каталог вывода, создаст защищённый профиль
владельца и готовый комплект для Docker. На целевые ПК переносите **папку Agent**
целиком — EXE и JSON вместе. После запуска подтвердите запрос в контроллере.
Знакомое устройство тоже запрашивает новое разрешение на каждое соединение.
Для Admin-инструментов нужен явный запуск от администратора; автоповышения нет.

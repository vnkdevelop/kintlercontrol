# KintlerControl 1.5.1 — start here / начните здесь

## What you downloaded

```text
clients/windows/          KintlerController.exe + KintlerAgent.exe + SHA256.json
server/                   portable Docker image, Compose, proxy/bootstrap templates
skills/kintler-remote/     portable AI instructions and bootstrap helper
docs/                     deployment, terminal, AI setup and layout guides
MANIFEST.json             version and per-file checksums
LICENSE
```

This is a generic installation kit: no server address, owner key, device identity or database is included. Verify the download against the release's `SHA256SUMS.txt`. Both clients work as one EXE each; the supporting folders are for initial setup, not runtime sidecars.

## Human setup

1. Prepare your own Linux amd64 server with Python 3.10+, Docker Engine + Compose and a DNS hostname. Automatic HTTPS needs free, reachable TCP 80/443. If you already have a proxy, do not replace it.
2. On the owner Windows PC, run `clients/windows/KintlerController.exe`.
3. Enter `/setup https://relay.example.com D:\Your-Kintler-Deployment`, replacing the example origin/output. Choose a new or empty output directory. For an existing proxy add `--mode existing-proxy`.
4. Transfer the generated `server-kit.zip` to a private application directory on your server and extract it, preserving `.env`. Run `python3 start_server.py --check`, then `python3 start_server.py`. The kit contains this standalone launcher, the Docker image and its SHA-256 file. The script loads the image or pulls the configured published image, downloads missing Caddy images, starts Compose and checks relay health. Owner private keys remain on the owner PC under Windows DPAPI. Once the backend is ready, use `/reconnect` in Controller or reopen it.
5. Copy only the generated `Agent/KintlerAgent.exe` to a target Windows PC and run it. Do not distribute the unconfigured generic Agent instead.
6. In Controller, open `/approve`, select that device and enter its fresh six-digit Agent code. Then `/use` selects an online device. This approval is required on every connection, including known devices.
7. `/help` lists commands; Ctrl+V pastes. `/screenshot` downloads/opens an image; `/message your text` shows a Windows message window. OS privileges and enabled tool policy still apply.

## Optional AI setup

1. In Controller, run `/install-skill`, or launch `KintlerController.exe --install-skill --host all`. Only generic skill files are installed into supported local AI hosts; owner keys and MCP settings are not copied.
2. Ask your AI: "Use Kintler on DEVICE". It should activate `kintler-remote`, locate the existing Controller and register stdio MCP in its own host. It does not need to read this project's source code or set up SSH.
3. Host trust prompts remain human decisions. A saved MCP config is not success: verify MCP initialize, tools/list and real `nodes()`/`capabilities()` calls.
4. Device approval remains yours. The AI cannot obtain the Agent's code or approve access through MCP.

For host-specific setup see `docs/AI_SETUP.md`. A local Windows process with the owner profile is required; a cloud/HTTP-only host needs a separately agreed adapter. The relay URL is not an MCP endpoint.

## Русский

- Запустите `clients/windows/KintlerController.exe` на ПК владельца.
- `/setup ВАШ_HTTPS_АДРЕС ПУСТОЙ_КАТАЛОГ` создаёт отдельный server kit и настроенный Agent. Для существующего прокси — `--mode existing-proxy`.
- На сервер отправляйте созданный `server-kit.zip`, а не личный профиль Controller. После распаковки: `python3 start_server.py --check`, затем `python3 start_server.py`. Нужны заранее установленные Python 3.10+, Docker и Compose; скрипт сам загрузит образ и запустит контейнеры. После запуска выполните `/reconnect` в Controller.
- На управляемый ПК переносите **только сгенерированный Agent.exe**. Каждый новый вход подтверждайте в `/approve` по его 6-значному коду. `/use` выбирает доступный ПК.
- Человеку MCP не нужен. Для AI установите `/install-skill`, затем попросите агента работать через Kintler. Он настроит свой stdio MCP и проверит инструменты, но не подтвердит устройство за вас.
- Не публикуйте созданные профили, server kit, ключи, аудит или настроенный личный Agent. Скачанный исходный комплект универсальный; созданные после Setup файлы — персональные.

Updates must preserve Docker data volumes. Never use `down -v` for an update. Read `docs/DEPLOYMENT.md` and `docs/PROJECT_LAYOUT.md` before changing an existing deployment.

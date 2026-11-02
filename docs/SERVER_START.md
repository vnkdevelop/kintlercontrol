# Start your extracted server kit

## Prerequisites

- A Linux amd64 server with Python 3.10+, Docker Engine and the Docker Compose plugin.
- Permission to use Docker and read your deployment files.
- Your own DNS hostname, reachable TCP 80/443 and free listeners for automatic Caddy HTTPS.
- Internet access for missing images such as Caddy and public certificate issuance. The Kintler backend image itself is included in the full bundle.

The launcher does not install the operating system, Docker, Compose or Python. It does not require source-code installation, SSH credentials or a Controller executable on the server.

## First startup

1. On the owner Windows PC, extract the full bundle and run `clients/windows/KintlerController.exe`.
2. Use `/setup YOUR_HTTPS_ORIGIN EMPTY_OUTPUT_DIRECTORY`. Add `--mode existing-proxy` if you already have HTTPS infrastructure.
3. Upload the generated `server-kit.zip` to a dedicated server directory, such as `/opt/kintlercontrol`, and extract its contents there. Preserve the hidden `.env` file.
4. In that directory, run `python3 start_server.py --check`. It checks the kit, Docker/Compose access, project ownership, automatic-HTTPS listeners and the bundled image checksum/architecture without starting containers or pulling images.
5. Run `python3 start_server.py`. It loads the verified `kintlercontrol-image.tar` when present, or pulls the configured image when no tar exists, downloads missing Compose images, starts only this project and waits up to 120 seconds for relay health.
6. Verify your external HTTPS origin and certificate. Relay health alone does not prove public DNS/certificate readiness.
7. In Controller, use `/reconnect`; start the generated `Agent/KintlerAgent.exe` on a target Windows PC, approve its fresh code through `/approve`, then select it with `/use NAME`.

The launcher preserves `.env`, owner registration, data volumes and unrelated services. If startup fails, it stops with a diagnostic rather than deleting data or replacing your existing web server. Inspect this project's Compose logs before retrying; do not use `down -v` for an update.

## Русский

После `/setup` отправьте созданный `server-kit.zip` на свой сервер и распакуйте, сохранив `.env`. Внутри уже есть `start_server.py`, Compose, публичная регистрация владельца, образ Kintler и его SHA-256.

Выполните `python3 start_server.py --check`, затем `python3 start_server.py`. Скрипт загрузит образ, подтянет недостающие образы Docker, запустит Compose и проверит relay. Docker, Compose и Python должны быть установлены заранее. Адреса, ключи и службы другого пользователя для этого не нужны.

При занятых 80/443 не останавливайте свои сайты: используйте режим существующего прокси. Для автоматического HTTPS нужен настоящий настроенный домен. После перезапуска relay снова подтверждайте подключения Agent свежими кодами.

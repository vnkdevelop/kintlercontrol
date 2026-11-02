# Настройка в текущей агентной среде

Читай только раздел своего клиента. `EXE` ниже — найденный реальный абсолютный путь к `KintlerController.exe`, не буквальная строка для запуска. Примеры с `C:/path/to/` показывают форму: замени путь перед сохранением. Для любой среды контракт одинаков: **локальный stdio**, EXE + `--mcp`, действующий профиль владельца на Windows-ПК. Не создавай HTTP/SSE endpoint из адреса backend.

Для новой установки EXE берётся из `%LOCALAPPDATA%\KintlerControl\installation.json` либо локального `references/local-installation.json` установленного скилла; не ищи его через реестр или процессы. Эти персональные JSON не входят в публичные пакеты и не должны коммититься. Используй actual command уже существующей MCP-записи, если она явно выбрана пользователем; карта не разрешает менять чужие профили или транспорт.

Сначала используй нативный MCP API/менеджер/CLI: он сохраняет другие настройки. При правке файла просмотри только нужный фрагмент, меняй одну запись, сохраняй остальные поля/комментарии; не выводи секреты. Локальные конфиги с персональным путём не коммить. Переменные путей разрешены только там, где конкретный клиент их раскрывает. Не включай глобальный auto-approve, `trust=true` или обход sandbox ради подключения.

## OpenCode V2

Проектный `opencode.json(c)` либо `.opencode/opencode.json(c)`: запись `mcp.servers.kintler`, `type="local"`, `command=[EXE,"--mcp"]`. Подтверждённое соединение проверяется через `opencode mcp list`/`/mcps`; `/reload` обновляет конфигурацию, перезапуск приложения не нужен.

```bat
cmd.exe /c opencode mcp add kintler -- "{env:LOCALAPPDATA}\Programs\KintlerControl\KintlerController.exe" --mcp
cmd.exe /c opencode mcp list
```

Это путь стандартной установки; если EXE найден в другой папке, используй его путь, а не этот шаблон. `{env:LOCALAPPDATA}` раскрывает именно OpenCode. `--global` — только по запросу. Инструменты могут быть доступны через Code Mode, а не в прямом списке.

## Codex CLI / Codex IDE / общий Codex host

MCP хранится в `~/.codex/config.toml`; проектный `.codex/config.toml` действует в доверенном проекте. Настраивай выбранный scope, не меняй все поверхности сразу. CLI `codex mcp list` показывает регистрацию, а `/mcp` и реальный вызов инструмента подтверждают доступность.

```toml
[mcp_servers.kintler]
command = "C:/path/to/KintlerController.exe"
args = ["--mcp"]
startup_timeout_sec = 45
tool_timeout_sec = 180
```

Не предполагай раскрытие `%LOCALAPPDATA%` или синтаксиса OpenCode внутри TOML. Не меняй режимы подтверждения инструментов. Если конфигурация требует нового запуска сессии/обновления MCP, используй штатное действие Codex, не утверждай, что файл уже подключён.

## Claude Code

Предпочитай CLI, используя найденный EXE. `local` — личная настройка текущего проекта; `project` пишет общую `.mcp.json`, `user` применяется ко всем проектам. Для общего файла нельзя сохранять персональный путь.

```bat
cmd.exe /c claude mcp add --transport stdio --scope local kintler -- "%LOCALAPPDATA%\Programs\KintlerControl\KintlerController.exe" --mcp
cmd.exe /c claude mcp get kintler
```

При другой папке подставь найденный путь. `/mcp` показывает состояние и позволяет переподключить сервер; Tool Search может скрывать инструменты до поиска. `Pending approval` требует подтверждения пользователя, не установки другого клиента. Для проектной JSON-конфигурации запись находится в `mcpServers.kintler`, тип — `stdio`.

## Claude Desktop

Открой нативные Developer → Edit Config. На Windows обычный файл — `%APPDATA%\Claude\claude_desktop_config.json`. Добавь только запись `mcpServers.kintler`:

```json
{"mcpServers":{"kintler":{"command":"C:/path/to/KintlerController.exe","args":["--mcp"]}}}
```

Не предполагай раскрытие переменных в `command`. Для применения обычно нужен перезапуск Claude Desktop: сохрани работу и попроси пользователя перезапустить приложение, не завершай его процесс сам. Проверь сервер и реальный вызов в новой/обновлённой сессии.

## Cursor

Используй текущую `.cursor/mcp.json` проекта; `~/.cursor/mcp.json` — пользовательский scope по запросу. Запись `mcpServers.kintler`, тип `stdio`:

```json
{"mcpServers":{"kintler":{"type":"stdio","command":"${env:LOCALAPPDATA}/Programs/KintlerControl/KintlerController.exe","args":["--mcp"]}}}
```

При переносном EXE используй его путь. Cursor раскрывает `${env:NAME}` в `command` и `args`. Подключи/обнови сервер через Customize/MCP и проверь Available Tools; соблюдай действующий Run Mode и ограничения организации.

## Google Antigravity App / CLI / SDK

Для актуального Antigravity стандартные источники — `.agents/mcp_config.json` проекта и `~/.gemini/config/mcp_config.json` пользователя. App использует Settings → Customizations → Installed MCP Servers; доступны Refresh и переключатель сервера. SDK также обнаруживает проектный `.agents/mcp_config.json`.

```json
{"mcpServers":{"kintler":{"command":"C:/path/to/KintlerController.exe","args":["--mcp"]}}}
```

Используй фактически загруженный источник для своего продукта/версии. Не подменяй его настройками Gemini CLI или OpenCode; не угадывай старые IDE-пути. Если установленная версия использует другой файл, найди его через её менеджер MCP. Не расширяй политики `mcp(*)` и не отключай sandbox.

## Gemini CLI

Только для фактического Gemini CLI, не для Antigravity: запись `mcpServers.kintler` в `.gemini/settings.json` проекта либо пользовательском `~/.gemini/settings.json`.

```json
{"mcpServers":{"kintler":{"command":"C:/path/to/KintlerController.exe","args":["--mcp"]}}}
```

`gemini mcp list` и `/mcp` проверяют состояние. Нативный `gemini mcp add` можно использовать после проверки его scope/синтаксиса в `--help`. Не предполагай, что переменные `env` автоматически раскрываются и в `command`; не добавляй `trust=true`.

## VS Code / GitHub Copilot

Сначала определи активный harness. Codex/Claude-расширение может использовать собственную конфигурацию из разделов выше, а не VS Code MCP. Для Copilot portable-формат — `.mcp.json` проекта либо пользовательский `$COPILOT_HOME/mcp-config.json` / `~/.copilot/mcp-config.json`; корень `mcpServers`, запись как в простом JSON выше.

Совместимый Local harness VS Code читает `.vscode/mcp.json` либо profile `mcp.json`; здесь корень **servers**, не `mcpServers`:

```json
{"servers":{"kintler":{"type":"stdio","command":"${env:LOCALAPPDATA}/Programs/KintlerControl/KintlerController.exe","args":["--mcp"]}}}
```

В portable Copilot-формате переменные имеют вид `${LOCALAPPDATA}`, а в VS Code-формате — `${env:LOCALAPPDATA}`; не смешивай их. Используй MCP: Add Server / MCP: List Servers, выбери Command (stdio), затем Start/Restart только Kintler и проверь вызов в своей сессии. Подтверждение доверия/Workspace Trust выполняет пользователь, не обходи его настройками.

## Другой MCP-клиент или агентный SDK

Это не закрытый список поддерживаемых сред. Найди нативную операцию регистрации **stdio** процесса и передай `command=EXE`, `args=["--mcp"]`. Имя корневой секции, файловый путь и способ обновления бери из схемы текущего host, не выдумывай `mcpServers` для любого SDK. Если схема неизвестна, выясни название/версию либо получи её через встроенную справку/официальную документацию только этого клиента. Не изучай исходники Kintler заново и не устанавливай OpenCode как обходной путь.

Если host поддерживает только удалённый HTTP MCP или не имеет доступа к Windows-процессу с профилем владельца, прямое подключение этого EXE невозможно. Сообщи конкретное ограничение и согласуй защищённый транспортный адаптер отдельно; не публикуй stdio-мост, ключи или новые порты автоматически.

## Общая проверка

После регистрации требуются успешный MCP initialize, tools/list и реальный `session_status()` из этой среды; для старого Controller — `nodes()`/`capabilities()`. Новая сборка содержит 14 MCP-инструментов, старые — 13; разрешения host могут скрывать часть, не отменяй их ради полного списка. Размер capabilities.tools — права backend, не размер MCP-каталога. `Added`, сохранённый JSON/TOML, удачный --probe или статус enabled не доказывают доступность MCP агенту. Сохраняй DPAPI-профиль и ручной ввод нового Agent-кода в Controller.

## Проверенные источники

- OpenCode V2: https://opencode.ai/v2/docs/mcp-servers
- Official OpenAI documentation, Codex MCP: https://developers.openai.com/codex/extend/mcp
- Claude Code: https://code.claude.com/docs/en/mcp
- Claude Desktop / local stdio: https://modelcontextprotocol.io/docs/develop/connect-local-servers
- Cursor: https://cursor.com/docs/context/mcp
- Antigravity: https://antigravity.google/docs/mcp/ и https://antigravity.google/docs/customization-discovery/
- Gemini CLI: https://geminicli.com/docs/tools/mcp-server/
- VS Code/Copilot formats: https://code.visualstudio.com/docs/agents/reference/mcp-configuration

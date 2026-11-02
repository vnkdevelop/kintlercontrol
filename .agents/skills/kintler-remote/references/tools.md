# MCP-параметры: читать только нужный инструмент

Каталог текущего host — источник истины. В версии 1.6.0 есть `open` и `desktop` (16 инструментов); 1.5.2 имеет 14, более старые Controller могут не иметь `session_status` и используют `nodes()` + `capabilities()` для проверки. Ничего не импортируй из исходников Kintler ради этих вызовов.

| Вызов | Назначение и ограничения |
| --- | --- |
| `session_status(node="", required_tools=[])` | Backend health, компактные устройства, единственная цель, эффективные права и рекомендуемый interpreter за один read-only вызов. `ok` и `selection` проверяются до действий. Не выбирает первый ПК, не выполняет sysinfo/shell и не одобряет доступ. |
| `nodes()` | Разрешённые устройства; это не сканирование сети. |
| `capabilities()` | Права оператора, не полный каталог MCP и не права каждого устройства. |
| `sysinfo(node)` | Подробные ОС/ресурсы/диски/сеть/права — только когда нужны по задаче, не обязательная проверка перед каждым действием. |
| `shell(node, command, interpreter="cmd", cwd=".", timeout=120)` | Windows — cmd, Linux — bash. PowerShell только если разрешён правилами. Таймаут 1–120 секунд. Новый процесс на каждый вызов, вывод ограничен. |
| `message(node, text)` | Явно запрошенный текст на интерактивном Windows desktop. До 2000 символов; использует shell/PowerShell, не повышает права; ждёт OK/отмену или 120 секунд. |
| `open(node, app, arguments=[])` | Запуск приложения на выбранном ПК: имя EXE или путь, аргументы отдельными строками, без shell. Нужны open и интерактивный desktop. Принятый запуск не доказывает выполнение последующей задачи. |
| `desktop(node, action, params={}, headed=False, timeout=60)` | Встроенный Windows agent-desktop: help, компактное дерево, поиск/refs, ввод, клики, курсор, drag, ожидание, batch до 16 шагов. Нужны desktop; физический ввод — headed=True. Цикл задачи выполняет ИИ, не сервис. |
| `ps(node, sort="ram", limit=100)` | ram/cpu/pid; limit 1–500. PID сохраняется вместе с created. |
| `kill(node, pid, created, force=False)` | Только разрешённый процесс; PID/created из свежего ps именно этого устройства. Native Windows может завершать сразу даже с force=False. |
| `upload(node, local_path, remote_path, overwrite=False)` | Controller host → workspace устройства; SHA-256 проверяется автоматически. |
| `download(node, remote_path, local_path, overwrite=False)` | Workspace устройства → Controller host; SHA-256 и атомарная запись. |
| `screenshot(node, local_path="", monitor=1)` | Без пути возвращает видимое MCP-изображение до 1600px и масштаб/смещение для координат; с путём сохраняет исходный PNG на Controller host. 1 — первый монитор, 0 — весь desktop. Нужны screenshot/download. Копия остаётся в remote workspace. |
| `registry(node, key, operation="query", name="", value="", value_type="REG_SZ")` | Query по ACL, set с admin. HKLM/HKCU/HKCR/HKU/HKCC. REG_SZ/EXPAND_SZ/DWORD/QWORD/MULTI_SZ/BINARY. |
| `services(node, operation="list", name="")` | list/status по ACL; start/stop с Admin/root; restart только Linux. Системное имя, не отображаемая подпись. |
| `tasks(node, operation="list", name="", command="")` | Windows list по ACL; create ONLOGON с admin и разрешением пользователя. Нет delete/run/update. |

`required_tools=["message"]` проверяет существующее разрешение shell. Наличие инструмента не заменяет разрешение пользователя на мутацию и не выдаёт права ОС.

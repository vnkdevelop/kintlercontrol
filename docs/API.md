# Kintler protocol v1

## Обязательное подтверждение соединений в backend 1.5.0

Каждый агентский WebSocket проходит две независимые стадии: проверка HMAC/Ed25519 → решение владельца по конкретному socket/session. До решения сервер не регистрирует активный узел и не маршрутизирует команды. Знакомый публичный ключ не пропускает стадию подтверждения.

- `GET /api/approvals`: requests[] только для контроллеров с `can_approve=true` и ACL на соответствующий NodeID.
- `POST /api/approvals/{request_id}`: `{"approve":true,"code":"123456"}` или `{"approve":false}`. Каждое подключение, включая знакомый ключ, требует нового 6-значного ASCII-кода из терминала нужного Agent и явного решения владельца в Controller. Отказ не требует кода.
- RequestID одноразовый и связан с одной живой socket/session. Закрытый, решённый или истёкший запрос не разрешает последующее соединение.
- Время ожидания — 600 секунд, максимум 64 ожидающих запроса/4 на IP; 5 неверных кодов завершают запрос отказом.
- Сервер присылает агенту `approval_required` с request_id/code/expires, затем `authenticated` только после разрешения либо `approval_denied`. До решения агент отправлять команды/результаты не должен.
- Код не выдаётся в GET /api/approvals и не записывается в аудит. Для каждого подключения его нужно получить с физически нужного Agent.exe; подключение MCP не подтверждает устройство автоматически.

## Ed25519 для новых агента и владельца

Wire login содержит `auth=ed25519`, `public_key` (base64 32 bytes), `proof` (base64 64-byte signature), а также прежние version/nonce/timestamp/role/principal/node_id. Подписывается канонический ASCII JSON с ключами `v,nonce,timestamp,role,principal,node_id,auth,public_key`, сортировкой ключей и компактными разделителями.

Agent role: неизвестный ключ может только запросить подтверждение. NodeID/principal для нового агента совпадают. Public key фиксируется после решения владельца; смена ключа снова требует кода. Controller role: public key должен быть заранее зарегистрирован администратором backend — автоматической регистрации владельцев нет. Для контроллера node_id пустой, подпись связана с role=controller.

Приватные ключи остаются на своих ПК под Windows DPAPI. Backend-комплект содержит только публичный ключ владельца. TLS 1.3 сохраняется; Ed25519-аутентификация не превращает передачу команд в E2E encryption.

Все рабочие endpoints доступны по HTTPS/WSS TLS 1.3 через доверенный reverse proxy. `/healthz` — только состояние процесса без идентификационных данных. `Cache-Control: no-store`. Секреты никогда не передаются в query string.

## Вход REST-контроллера

1. `POST /api/auth/challenge`, без Authorization. Ответ: `type=challenge`, `version=1`, `nonce`, целочисленный `timestamp` сервера.
2. Вычислить HMAC-SHA256 своим операторским ключом над ASCII JSON с `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`:

```json
{"v":1,"nonce":"SERVER_NONCE","timestamp":0,"role":"controller","principal":"OPERATOR_ID","node_id":""}
```

Использовать фактические `nonce`/`timestamp`; ключи JSON сортируются при канонизации. Код совместимого клиента: `common.protocol.auth_message`/`proof`.

3. `POST /api/auth/login`:

```json
{
  "type":"authenticate",
  "version":1,
  "nonce":"SERVER_NONCE",
  "timestamp":0,
  "role":"controller",
  "principal":"OPERATOR_ID",
  "node_id":"",
  "proof":"HMAC_HEX"
}
```

4. Ответ содержит `token`, `session_id`, UNIX `expires`. Для остальных REST-запросов: `Authorization: Bearer <token>`. Не логировать ответ login.
5. `POST /api/auth/logout` отзывает текущую сессию и отменяет её активные команды.

Nonce одноразовый, привязан к IP, TTL 30 секунд. Пять неверных попыток с IP дают временную блокировку на 15 минут. Ключи случайные, минимум 32 символа. Агентский ключ не даёт входа контроллера.

## REST endpoints

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/healthz` | Health check |
| GET | `/api/nodes` | Только разрешённые узлы; `nodes[]` с id/name/platform/admin/online/last_seen/tools/telemetry |
| GET | `/api/tools` | Операторские tools, nodes, session_id, expires |
| POST | `/api/nodes/{node_id}/cmd` | `{"tool":"sysinfo","args":{},"timeout":120}`; HTTP 202 с command_id |
| GET | `/api/commands/{command_id}?after=0&wait=20` | Длинный poll; события и конечный результат только владельцу сессии |
| POST | `/api/commands/{command_id}/cancel` | Идемпотентная отмена собственной команды |

Ошибки приложения возвращают `{"error":"..."}`; auth — 401, TLS policy — 403, неверные аргументы/ACL/неизвестный ресурс — 400, capacity WebSocket — 503. Proxy может возвращать собственные HTTP ошибки/не-JSON ответы; SDK обрабатывает их явно. Не повторять автоматически команды после сетевой ошибки: неизвестно, исполнилась ли уже операция.

## События команды

```json
{
  "type":"event",
  "session_id":"sess_SERVER_GENERATED",
  "target_node_id":"node_DEVICE",
  "command_id":"cmd_SERVER_GENERATED",
  "seq":1,
  "kind":"output",
  "stream":"stdout",
  "data":"text"
}
```

Конечное событие: `kind=result`, `result={"ok":true,...}`. Shell возвращает `exit_code`; ошибка инструмента — `ok=false,error`. REST envelope содержит `finished`, `result`, `events`, `first_seq`, `next_seq`. `after` — последний прочитанный seq, `wait` — 0..20 секунд. Если `first_seq > after + 1`, часть буфера вытеснена: прекратить чтение с ошибкой, не выдавать неполный вывод за полный.

Все routing IDs назначает сервер; клиент не выбирает session_id или command_id. Ответ агента проверяется по узлу, текущей агентской сессии, исходной операторской сессии и command_id. Старые/завершённые ответы игнорируются; подмена канала закрывает агентское подключение.

## WebSocket

Endpoints: `/ws/controller`, `/ws/agent`. Первый серверный frame — challenge. Клиент отправляет HMAC login JSON как выше, ответ — `type=authenticated,session_id,expires`.

Агент дополнительно указывает привязанный `node_id` и `metadata` (name/platform/user/admin/version/tools/telemetry), затем каждые 10 секунд отправляет `{"type":"heartbeat","metadata":{...}}`. Relay отправляет команды/отмену. Дубликат соединения одного активного NodeID отклоняется, а не заменяет рабочую сессию.

Контроллерские сообщения требуют уникальный `request_id`:

```json
{"type":"nodes","request_id":"r1"}
{"type":"use","request_id":"r2","node":"WORKSTATION"}
{"type":"command","request_id":"r3","tool":"sysinfo","args":{},"timeout":120}
{"type":"cancel","request_id":"r4","command_id":"cmd_SERVER_GENERATED"}
```

Ответы — `type=reply,request_id` и nodes/node/command_id/ok либо error. События command могут прийти до reply, поэтому обработчик потока должен допускать эту гонку. `use` увеличивает server-side generation, отменяет прежние команды и не пересылает их остаточные события в новую generation.

## Аргументы инструментов

| tool | args |
|---|---|
| shell | `command`, `shell`=cmd/powershell/bash, относительный `cwd` |
| sysinfo | `{}` |
| ps | `sort`=ram/cpu/pid, `limit`=1..500 |
| kill | `pid`, `created` из ps, `force` bool |
| screenshot | `monitor` int, 1 — первый, 0 — все; результат содержит workspace path/size/sha256 |
| registry | `operation`=query/set, `key` с локальным HKLM/HKCU/...; для set: name/value/value_type |
| services | operation=list/status/start/stop, Linux также restart; name для операции над службой |
| tasks | operation=list/create; create: name, command; создаётся ONLOGON-задача Windows |

MCP `screenshot` также принимает `local_path` и выполняет последующее скачивание. `upload`/`download` MCP принимают локальные/удалённые пути и используют SDK для всех чанков.

### Upload low-level

1. `operation=start,path,size,sha256` → upload_id, chunk_size.
2. `operation=chunk,upload_id,offset,data` (строгий base64) → следующий offset; только последовательные чанки.
3. `operation=finish,upload_id,overwrite` → size/sha256. Без explicit overwrite существующая цель не заменяется.
4. `operation=abort,upload_id` для очистки.

UploadID принадлежит исходной операторской сессии; соседняя сессия не может продолжить/завершить его.

### Download low-level

1. `operation=stat,path` → size/sha256/chunk_size.
2. `operation=chunk,path,offset` → data/base64, offset, eof.
3. Проверить весь SHA-256 перед атомарным сохранением на управляющем ПК. Если файл изменился между чанками, завершить с ошибкой и удалить временный результат.

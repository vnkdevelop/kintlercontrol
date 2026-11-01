# AI setup / подключение AI

## The shortest supported path

The owner configures Controller and approves devices once per connection. The AI uses the same Controller EXE as a local stdio process:

```text
command: absolute path to KintlerController.exe
args:    --mcp
```

There is no separate MCP EXE, no SSH requirement, and no private key in the host configuration. Controller loads its active protected owner profile from user data. `--env PATH` is optional for a deliberately selected existing profile.

Install the generic skill with `/install-skill` or `KintlerController.exe --install-skill --host all`. `--dry-run` shows destinations; existing different skills are retained unless a managed Kintler skill update is explicitly requested with `--replace-existing`.

| Host | User skill source |
| --- | --- |
| Codex / shared Agent Skills hosts | `~/.agents/skills/kintler-remote` |
| OpenCode V2 | `~/.config/opencode/skills/kintler-remote` |
| Claude Code / local Claude harness | `~/.claude/skills/kintler-remote` |
| Gemini CLI | `~/.gemini/skills/kintler-remote`, shared alias also supported |
| Antigravity App | `~/.gemini/config/skills/kintler-remote` |
| Installed legacy Antigravity IDE | compatibility folder under `~/.gemini/antigravity/skills` |
| Copilot CLI / VS Code local/Copilot hosts | `~/.copilot/skills/kintler-remote` |
| Cursor when installed | `~/.cursor/skills/kintler-remote` |

The installer copies only the generic skill and its supporting files. It does not install AI clients, change trust/sandbox policy, register MCP servers globally, create owner identities or approve devices. Codex compatibility skill folders already present are supported without relying on them as the primary modern discovery path.

Local skill discovery and cloud account skills differ. Claude Desktop/Cowork or a host requiring account upload must import `KintlerControl-Skill.zip` through its UI; copying a local folder does not prove a cloud skill is installed. The skill remains available for a compatible host's normal relevance-based discovery; activation is not guaranteed if that host disables custom skills.

## Ask your agent to connect

"Use Kintler on DEVICE" lets the agent configure the needed local MCP in its current host, not all unrelated hosts. The skill directs it to:

1. Find already available Kintler tools; use the actual namespace or Code Mode catalog.
2. Reuse the existing Controller. The full bundle has `clients/windows/`; a missing Controller can be bootstrapped by the skill's checksum-verified Windows script, without downloading the backend image.
3. Use the host's native MCP manager/CLI and preserve every unrelated server and permission. `KintlerController.exe --mcp-config --host HOST` prints the correct connection entry; it never writes settings.
4. Respect host trust prompts and verify initialize, the 13-tool catalog and real `nodes()` / `capabilities()` calls.
5. Select an explicitly requested online device by its exact ID. Verify operator/device tool policy before each operation. Missing/ambiguous targets require clarification.

Read only the matching section of `skills/kintler-remote/references/mcp-clients.md` for the host's schema. OpenCode uses `mcp.servers` and an executable array; Codex uses TOML; other hosts may use `mcpServers` or `servers`. These shapes are not interchangeable.

## Boundaries

- MCP has no approval tool. A human reads the Agent code and enters it in `/approve`.
- Installing Controller does not grant an owner key. For an existing backend, select its existing owner profile; never generate a replacement owner.
- File `local_path` is on the Windows machine running Controller/MCP; `remote_path` is inside the selected Agent workspace.
- `message(node,text)` uses existing shell permission and an interactive Windows desktop. It preserves normal process containment and waits for OK/cancel or 120 seconds.
- Destructive changes need the user's authorization; do not bypass disabled tools through shell or silently elevate.
- Cloud/Linux/macOS hosts cannot directly run a Windows EXE unless they have a supported Windows executor. Do not publish a bridge, keys or extra ports without separate authorization.

## Русский

Установите скилл через `/install-skill`, затем попросите AI «работай через Kintler на ИМЯ». Агент должен подключать MCP именно своей среды и проверять реальные инструменты, а не просто сохранить JSON. Ключ владельца остаётся под DPAPI вне EXE и конфигов AI. Доступ к устройству подтверждает человек; загрузка скилла или установка Controller сама его не выдаёт.

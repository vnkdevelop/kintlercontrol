# KintlerControl 1.6.3

- Automatically expire audit records after 30 days and retain at most 100,000 records. Configure positive limits through `KINTLER_AUDIT_RETENTION_DAYS` and `KINTLER_AUDIT_MAX_ROWS` in the server's local `.env`.
- Enforce the row cap in the same transaction as each audit write. Expiry cleanup runs at startup and hourly, draining old records in bounded 1,000-row batches without deleting device identities or registrations.
- Reuse freed SQLite pages, enable incremental vacuum for new databases and limit retained journal size. Existing databases remain compatible without a blocking full-database migration.
- Refresh heartbeat metadata and liveness in memory on every pulse, but persist it at most once per minute and on disconnect. Approval, ACL and session-expiry behavior are unchanged.
- Fix Controller process-picker formatting and require the release workflow to pass the same formatting check as CI.

## Preserved 1.6.2 functionality

- Stabilize process-picker mouse tests by waiting for the first rendered frame before calculating click coordinates. Runtime process selection and cancellation behavior are unchanged.
- Fix application lookup for `/open zabor.exe` and other installed apps not registered in PATH/App Paths: search the current user's and common Start menu shortcuts, matching the app/shortcut name or target EXE.
- Read shortcut targets through Windows ShellLink without resolving or modifying the shortcut. Preserve shortcut arguments and working directory, then append the caller's literal arguments.
- Deduplicate identical targets, refuse ambiguous matches and bound the search. Only file/path-not-found triggers lookup; permission errors never try alternative programs.
- Preserve explicit executable paths, URI launches, device ACL, cancellation and the single native Agent EXE. Regression tests cover the real ShellLink reader and short-name GUI launch.
- `/kill` without arguments opens a searchable, alphabetical process picker showing names only. Selection preserves the exact PID and creation time internally; Esc/Ctrl+C and target switches cancel without termination. Explicit PID/time arguments and `--force` remain supported.
- Refine the AI desktop workflow around semantic UI Automation, bounded batches and minimal result verification. Reuse display geometry when coordinate input is needed; screenshots require an explicit request or necessary visual information unavailable through the accessibility tree.

## Preserved 1.6.0 functionality

- `/open` in Controller and `open` over MCP launch applications on the explicitly selected device. Literal argument arrays do not run through a shell.
- New `desktop` tool embeds the pinned Apache-2.0 agent-desktop core/Windows libraries in the native Rust Agent. The managed PC still receives just one EXE, without Node.js, Python, a helper EXE or sidecar DLL.
- AI chooses the observe-act-verify loop for a user's multi-step GUI task. Compact accessibility snapshots/qualified refs, physical mouse/keyboard, drag, waiting and bounded stop-on-error batches are available. One on-demand help action replaces dozens of permanent MCP schemas.
- MCP screenshot without a destination returns an image plus coordinate scaling/monitor origin. Explicit destinations retain the previous PNG download behavior. Temporary Controller images are removed.
- New tools use the existing relay ACL, local tool policy, fresh human pairing and audit. Screenshot/download stay separately permissioned. Desktop input is serialized; self-worker processes are bounded/cancellable and use session-scoped private ref state.
- Existing deployments must explicitly add open/desktop to the owner and Agent tool allowlists if restricted. No automatic access-policy migration or deployment is performed. Native Windows is the supported embedded GUI target; source Python hosts can use their configured native Agent worker.
- The universal bundle includes updated clients/backend/skill and agent-desktop license/attribution.

## Preserved 1.5.2 functionality

Client and AI-integration release: the Windows Controller, MCP surface and portable skill changed; relay logic is the same as 1.5.1.

- MCP exposes 14 tools. New read-only `session_status(node, required_tools)` returns backend health, compact permitted devices, the uniquely matched target, effective tool rights and the recommended interpreter in one call. It runs no shell/sysinfo on the device, selects no default target and never approves access.
- Exact NodeIDs from a fresh inventory are reused for up to three seconds inside one SDK session; names still resolve against fresh inventory and the backend re-checks ACL and online approval on every command.
- Controller gained `--probe` (read-only connection/device JSON without device commands or result files), `--installation-info` and complete `--help` coverage of MCP/skill/setup/deploy modes. JSON output is always UTF-8.
- New local installation map at `%LOCALAPPDATA%\KintlerControl\installation.json` records the actual (including portable) Controller path, chosen Deployment folders, server kit/launcher/image, generated Agent and owner-profile path. It stores no relay origin, device identity or credential value and is excluded from Git, Docker context, public ZIPs, the wheel and the embedded skill.
- Skill installation writes `references/local-installation.json` inside each local skill copy as a pointer to that shared map, so moving and reopening Controller updates one file.
- Skill rewritten around a fast path: one `session_status` call before device work, no process/registry/disk discovery, no SDK/venv substitution. Tool parameters moved to `references/tools.md`; OS restrictions (ACL, AppLocker/GPO) are handled in `references/troubleshooting.md` only after a real error, and confirmed administrator blocks are never bypassed.
- Generated server kits keep the standalone `start_server.py` launcher with SHA-256-verified image loading, registry fallback, Compose startup and bounded health checks.
- Controller status remains `Kintler подключён · доступно N` or `Kintler не подключён · доступно 0`; missing-target errors point to `/use имя ПК`.

- Native Rust agent with no embedded relay address or client identity.
- Controller contains terminal UI, setup, MCP, skill installation, host-specific MCP config output and deployment modes in the same EXE.
- Setup creates a configured single-file Agent.exe with only a public endpoint, not credentials or a JSON sidecar.
- Local protected profile discovery survives executable relocation; native desktop shortcuts are supported.
- Automatic HTTPS through Caddy, or an existing HTTPS reverse proxy.
- Both apps use black/forest-green terminal interfaces with the supplied white Controller / green Agent icons; the native Rust Agent shows a large six-digit code, then plain actions without row numbers or actor labels.
- Controller connects saved profiles automatically, lists short device names, provides command descriptions/completion and bottom-right connection notifications instead of persistent configuration forms.
- Every new agent connection requires explicit owner approval and a fresh six-digit code from Agent entered in Controller, including known devices.
- Read-only registry/service/task inspection respects OS ACL; mutations retain Admin/root and authorization checks. Entered Controller commands run without a second confirmation popup; fresh device-code approval remains mandatory.
- Full online-device picker, masked approval-code entry, Windows clipboard paste, screenshot progress/default viewer and literal Russian-capable message windows are included.
- MCP also includes `message` using the existing shell permission without changing the relay protocol.
- Obsolete Tk UI, tool-form metadata and unused generated leaf artwork were removed; compatibility entrypoints, Linux/Python tooling, security tests and current icon sources remain.
- Portable AI skill installs a missing Controller EXE with SHA-256 validation and directs native-host stdio setup on request. Skill installation never grants an owner identity or device access; manual control does not need MCP.
- Ed25519 identities, local Windows DPAPI protection, bounded commands and SHA-256 file transfer.
- Standalone controller with integrated MCP bridge; no Python/Rust installation on client PCs.
- Personal configured installations are not part of the repository or release assets.

Extract KintlerControl-Bundle.zip on the owner PC and run clients/windows/KintlerController.exe. Follow START_HERE.md.
The included backend image supports offline loading before Docker Compose starts.
Read the requirements and security boundaries in README and SECURITY.md.

# KintlerControl 1.5.1

Self-hosted release with one complete generic bundle, two self-contained Windows EXE files, a portable Docker backend and AI skill integration.

- Generated server kits now include standalone `start_server.py`: read-only checks, SHA-256-verified image loading or registry pulling, missing Caddy image downloads, Compose startup and bounded relay-health checks. Python/Docker/Compose remain explicit server prerequisites; no OS installation is silently attempted.
- Controller status is now only `Kintler подключён · доступно N` or `Kintler не подключён · доступно 0`, independent of the selected device. Missing-target errors point directly to `/use имя ПК`.
- Backend health now checks database access/write capability, maintenance-loop liveness and detected API failures instead of always returning `ok`.

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
- MCP now exposes 13 tools, including `message`, using the existing shell permission without changing the relay protocol.
- Obsolete Tk UI, tool-form metadata and unused generated leaf artwork were removed; compatibility entrypoints, Linux/Python tooling, security tests and current icon sources remain.
- Portable AI skill installs a missing Controller EXE with SHA-256 validation and directs native-host stdio setup on request. Skill installation never grants an owner identity or device access; manual control does not need MCP.
- Ed25519 identities, local Windows DPAPI protection, bounded commands and SHA-256 file transfer.
- Standalone controller with integrated MCP bridge; no Python/Rust installation on client PCs.
- Personal configured installations are not part of the repository or release assets.

Extract KintlerControl-Bundle.zip on the owner PC and run clients/windows/KintlerController.exe. Follow START_HERE.md.
The included backend image supports offline loading before Docker Compose starts.
Read the requirements and security boundaries in README and SECURITY.md.

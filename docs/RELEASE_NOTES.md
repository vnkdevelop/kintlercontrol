# KintlerControl 1.4.0

Self-hosted release with two self-contained Windows EXE files and a portable Docker backend.

- Native Rust agent with no embedded relay address or client identity.
- Controller contains GUI, setup, MCP and deployment modes in the same EXE.
- Setup creates a configured single-file Agent.exe with only a public endpoint, not credentials or a JSON sidecar.
- Local protected profile discovery survives executable relocation; native desktop shortcuts are supported.
- Automatic HTTPS through Caddy, or an existing HTTPS reverse proxy.
- Both apps now use black/forest-green terminal interfaces and green leaf icons; the native Rust Agent shows a large six-digit code, then a read-only operator/AI activity log.
- Controller connects saved profiles automatically, lists short device names, provides command descriptions/completion and bottom-right connection notifications instead of persistent configuration forms.
- Every new agent connection requires explicit owner approval and a fresh six-digit code from Agent entered in Controller, including known devices.
- Read-only registry/service/task inspection respects OS ACL without unnecessary forced elevation; mutations keep Admin/root checks and explicit operator confirmation.
- Project AI skill installs a missing Controller from the official release with SHA-256 validation and registers local MCP on request; manual control does not need MCP.
- Ed25519 identities, local Windows DPAPI protection, bounded commands and SHA-256 file transfer.
- Standalone controller with integrated MCP bridge; no Python/Rust installation on client PCs.
- Personal configured installations are not part of the repository or release assets.

Extract the Windows kit on the owner PC and run KintlerController.exe, then use Setup. Follow START_HERE.md.
The included backend image supports offline loading before Docker Compose starts.
Read the requirements and security boundaries in README and SECURITY.md.

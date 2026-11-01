# KintlerControl 1.3.1

Self-hosted release with two self-contained Windows EXE files and a portable Docker backend.

- Native Rust agent with no embedded relay address or client identity.
- Controller contains GUI, setup, MCP and deployment modes in the same EXE.
- Setup creates a configured single-file Agent.exe with only a public endpoint, not credentials or a JSON sidecar.
- Local protected profile discovery survives executable relocation; native desktop shortcuts are supported.
- Automatic HTTPS through Caddy, or an existing HTTPS reverse proxy.
- Every new agent connection requires explicit owner approval and a fresh eight-digit code from Agent entered in Controller, including known devices.
- Project AI skill installs a missing Controller from the official release with SHA-256 validation and registers local MCP on request; manual control does not need MCP.
- Ed25519 identities, local Windows DPAPI protection, bounded commands and SHA-256 file transfer.
- Standalone controller with integrated MCP bridge; no Python/Rust installation on client PCs.
- Personal configured installations are not part of the repository or release assets.

Extract the Windows kit on the owner PC and run KintlerController.exe, then use Setup. Follow START_HERE.md.
The included backend image supports offline loading before Docker Compose starts.
Read the requirements and security boundaries in README and SECURITY.md.

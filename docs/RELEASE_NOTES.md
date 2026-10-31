# KintlerControl 1.2.0

Self-hosted release with generic Windows clients and a portable Docker backend.

- Native Rust agent with no embedded relay address or client identity.
- Desktop setup wizard creates each user's owner identity and deployment kit.
- Automatic HTTPS through Caddy, or an existing HTTPS reverse proxy.
- Every new agent connection requires explicit owner approval, including known devices.
- Ed25519 identities, local Windows DPAPI protection, bounded commands and SHA-256 file transfer.
- Standalone controller and MCP bridge; no Python/Rust installation on client PCs.
- Personal configured installations are not part of the repository or release assets.

Extract the Windows kit on the owner PC and run KintlerSetup.exe. Follow START_HERE.md.
The included backend image supports offline loading before Docker Compose starts.
Read the requirements and security boundaries in README and SECURITY.md.

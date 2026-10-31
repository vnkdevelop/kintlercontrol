# Security policy

KintlerControl is an administrative remote-management tool. Only deploy it on
devices you own or are explicitly authorized to manage.

## Supported security model

- TLS 1.3 with certificate validation, individual client identities, owner ACLs.
- A fresh owner decision for every new agent socket/session, including known devices.
- New/changed agent keys require the code displayed by that agent.
- Windows private keys remain locally DPAPI-protected; server kits contain public registration only.
- The relay is trusted. No E2E encryption, TPM attestation, or shell sandbox is claimed.
- Full shell has the agent account's OS rights. Administrative tools require actual privilege.

The repository and generic release binaries must not contain deployment-specific
domains, machine identities, owner registrations, `.env` values, private keys,
databases, logs, or personal configured builds. Generated deployments are local
artifacts and must not be committed or uploaded as public release assets.

## Operational responsibilities

Keep Windows, Docker, images and client versions updated. Monitor disk space and
archive/backup the audit volume safely. Keep owner profiles private. DPAPI is not
a defense against malware in the original user's security context. Do not expose
the broker directly; use the configured trusted TLS proxy. Do not run multiple
relay replicas against in-memory sessions. Require AI confirmation for destructive tools.

Public EXE files are currently unsigned. Checksums detect unexpected differences
only when obtained from a trusted source; they are not publisher signatures.

## Reporting a vulnerability

Use the repository's private vulnerability reporting facility when available.
Do not publish real endpoints, credentials, private keys, machine IDs, captured
screenshots or audit data in an issue. If private reporting is unavailable, open
a minimal issue requesting a private contact channel without exploit details or secrets.

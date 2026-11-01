# KintlerControl project rules

## Canonical implementation and parity

- This repository is the canonical implementation for both public installations and personal deployments.
- After a request to change a personal Kintler service, implement the same functional change in the universal public implementation. Never maintain a personal-only feature or weaker public variant.
- Keep protocols, tools, approval policy, security checks, limits and error handling identical. Only deployment configuration, owner identities and server addresses may differ.
- Personal builds are the same generic code plus local configuration. Preserve legacy personal archives and active profiles; do not rewrite them without explicit authorization.
- Every new agent connection requires an explicit owner decision. A known key is not a permanent access grant.

## Privacy and publication

- Never commit or include in public releases real server addresses, SSH credentials, device identities, owner registrations, private keys, profiles, databases, screenshots or audit data.
- Credentials are read only from ignored local `.env` files. Values must not appear in tool output, command arguments containing passwords, logs or new tracked files.
- Keep personal deployment files under ignored `.private/`, `deployments/` or user-data directories. Do not put secrets in agent-rule files.
- Public clients must remain generic and work with any user's backend. Do not sacrifice a feature or security check to remove personal configuration.
- Before each push, scan staged content and Git history with the local privacy policy. Verify generated releases separately, including executable bytecode and container layers where applicable.

## Safe engineering

- Preserve working functionality. Limit changes to the requested task; no unrelated refactoring or destructive cleanup.
- Read each target fragment before editing and use unique context anchors. Do not rewrite working files from scratch.
- Do not add source comments or remove existing comments. Necessary compiler and linter directives are allowed.
- Validate syntax, types, tests, native Rust checks and packaged clients before declaring completion.
- Do not revert or delete changes you did not make. Remove only task-owned temporary files after verification.
- Clean up task-owned temporary scripts, installers used only for tooling, test identities/profiles, staging folders, intermediate binaries, native target directories and compiler/test caches immediately when verification finishes, including failed-attempt leftovers.
- End each task by checking that no temporary scripts or unused generated files remain. Explicitly distinguish retained deliverables/deployment backups from temporary files.
- Never classify user work, active `.env` profiles, live data volumes, required deployment reports, current deliverables or existing backups as disposable without explicit authorization.

## Execution and deployment

- On this Windows development environment run terminal commands through explicit `cmd.exe /c`; do not use the default PowerShell shell.
- SSH prefers keys and uses `-o BatchMode=yes -o StrictHostKeyChecking=accept-new`. Target/user/key path come from local `.env`, never tracked source.
- Before an authorized deployment, inspect existing services, containers, ports and networks. Do not stop, replace or reconfigure unrelated services.
- Back up the deployment's own configuration, preserve data volumes and provide rollback. Never use broad resets, recursive deletes, or production `down -v`.
- Personal service updates and public releases must use the same canonical version and receive equivalent functional verification.
- Do not deploy, publish or shut down a computer unless the user explicitly authorizes that action for the current task.

## Repository context and rule synchronization

- Query `graphify-out/graph.json` first when available, before listing or reading project code. After code/structure changes run `graphify update .`; graph output stays ignored.
- Keep root `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` semantically identical. OpenCode reads `AGENTS.md`, so it must be complete.
- Work directly unless the user or applicable instructions explicitly request delegation.

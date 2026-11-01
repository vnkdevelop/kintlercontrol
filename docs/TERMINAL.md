# Kintler terminal interface

Both Windows clients use a real terminal interface: black background, muted forest-green
highlights, a green leaf icon and keyboard-first interaction. The Agent stays native Rust;
there is no Python wrapper or runtime requirement on the managed Windows PC.

## Controller

Start the Desktop shortcut or the standalone Controller EXE. Its protected active owner
profile connects automatically. This is an application connection to the relay, not SSH;
there is no daily server-login form. Initial setup and profile selection remain commands.

- `/devices`: list short device names, online state and rights.
- `/use laptop` or `/use 2`: select one device. Long IDs remain internal routing keys.
- `/requests`: pending connections.
- `/approve 1 123456`: enter the six-digit code shown by the correct Agent.
- `/deny 1`: refuse a connection without granting access.
- `/help`: commands with descriptions and usage examples.
- `/sysinfo`, `/ps --limit 20`: system information and processes.
- `/shell echo hello`, or a plain command: run on the selected device after confirmation.
- `/upload local.bin remote.bin`, `/download remote.bin local.bin`: verified file transfer.
- `/screenshot capture.png`: capture a permitted interactive desktop.
- `/registry`, `/services`, `/tasks`, `/kill`: advanced operations with contextual help.
- `/permissions`: operator and device permissions.
- `/profile PATH`: choose an existing local owner profile; never replace a different owner.
- `/setup HTTPS_URL OUTPUT --mode existing-proxy`: create a separate personal deployment.
- `/mcp`: stdio configuration for any compatible AI client, using this same EXE.
- `/shortcut`, `/disconnect`, `/cancel`, `/clear`, `/exit`: local application controls.

Ctrl+P opens command suggestions, Ctrl+D lists devices, arrows navigate suggestions,
Tab completes a command, Ctrl+C cancels operations, Ctrl+Q exits. Connection requests appear
at the bottom right. Commands and results stay in the main transcript instead of tool panels.
Approval codes are not written into that transcript or stored in command history.

Destructive operations require confirmation for the selected device. Switching devices
cancels the Controller's in-progress work; commands are never redirected to the new target.
Registry/service/task inspection can run as User when OS ACL permits it. Changes still need
Admin/root; the software does not silently elevate itself or bypass permissions.

## Agent

Start the configured standalone Agent. A large six-digit code fills the centre of the terminal.
Before owner confirmation the device cannot accept commands. When connected, the code disappears
and a read-only activity transcript shows what the operator or AI is asking it to do.
Passwords/tokens and file-transfer payloads are not displayed in the activity summary.

Up/Down scroll recent activity; `r` reconnects and generates a new approval request;
`q` closes the Agent. Known devices still need a new code for every connection. Five wrong
codes or an expired request do not grant access. A short device name is shown, not a large ID.

The Agent redraws only on events/resizes, keeps a bounded activity buffer and refreshes
telemetry at the existing heartbeat interval. Files, authentication, cancellation and local
tool permissions continue through the same native engine.

## Compatibility

`KintlerController.exe --mcp`, `--setup`, `--deploy`, `--activate-profile` and
`--create-shortcut` remain modes of one EXE. MCP does not open a terminal window; its
stdio transport and protected profile are unchanged. Existing device profiles and keys
remain outside executable files under Windows DPAPI.

Source-only Python/Linux users can run the compatible terminal frontend with the `desktop`
extra installed. The published Windows Agent is still native Rust.

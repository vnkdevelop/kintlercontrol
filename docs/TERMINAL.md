# Kintler terminal interface

Both Windows clients use a real terminal interface: black background, muted forest-green
highlights, the supplied white Controller leaf / green Agent leaf icons and keyboard-first interaction. The Agent stays native Rust;
there is no Python wrapper or runtime requirement on the managed Windows PC.

## Controller

Start the Desktop shortcut or the standalone Controller EXE. Its protected active owner
profile connects automatically. This is an application connection to the relay, not SSH;
there is no daily server-login form. Initial setup and profile selection remain commands.

- `/devices`: list short device names, online state and rights.
- `/use` opens a live list of online devices; use arrows and Enter/Tab, or click a device. `/use laptop` and `/use 2` also work. Long IDs remain internal routing keys.
- `/requests`: pending connections.
- `/approve`: choose the pending device and enter its six-digit Agent code in the masked field.
- `/approve 1` asks for that device's code; `/approve 123456` or a bare six-digit code works directly for one pending request. Multiple requests always require an explicit device choice.
- `/deny 1`: refuse a connection without granting access.
- `/help`: commands with descriptions and usage examples.
- `/sysinfo`, `/ps --limit 20`: system information and processes.
- `/kill`: choose a current process from a searchable, alphabetical list showing names only (up to 500 processes). Arrows select; Enter or the Terminate button terminates that exact process; Esc/Ctrl+C cancels. PID and creation time stay internal to preserve PID-reuse protection. `/kill --force` uses the same picker with explicit force; `/kill PID creation_time [--force]` remains supported. Requires both ps and kill permissions for the picker.
- `/shell echo hello`, or a plain command: run immediately on the selected device, without an extra confirmation window.
- `/message Привет, устройство!`: show this literal text in a Kintler message window on the selected Windows desktop. Russian, quotes and punctuation are supported; no administrator rights or new backend tool are needed. The command waits for OK, cancellation or the 120-second limit; Controller remains usable. The process stays inside the Agent's normal cancellation/cleanup boundary.
- `/open notepad.exe`, `/open zabor.exe`, `open notepad.exe` or `/open "C:\Program Files\App\app.exe" "argument"`: open an application on the selected device, with literal arguments and no shell interpolation. Short names also match installed Start menu shortcuts and their target EXEs; shortcut arguments/working directory are preserved. Different matching targets require a full path, and permission failures never try an alternative. Requires open permission. MCP exposes the same operation.
- `/desktop help` and `/desktop snapshot {"app":"Notepad"}`: embedded desktop controls. For physical input use `{"headed":true,...}`. The AI uses the same desktop MCP function to interpret and carry out a multi-step GUI task; the device runs no model and needs no extra executable. Commands are bounded, audited and cancelled on target switches.
- `/upload local.bin remote.bin`, `/download remote.bin local.bin`: verified file transfer. Missing file parameters open a path form; download destination defaults to Downloads/KintlerControl. Destination directories are created as needed.
- `/screenshot`: save a permitted interactive desktop to Pictures/KintlerControl and open the PNG in the local default image viewer; an explicit PNG path is optional. Progress, save location and viewer errors are displayed.
- `/registry`, `/services`, `/tasks`: advanced operations with contextual help.
- `/permissions`: operator and device permissions.
- `/profile PATH`: choose an existing local owner profile; never replace a different owner.
- `/setup HTTPS_URL OUTPUT --mode existing-proxy`: create a separate personal deployment.
- `/mcp`: stdio configuration for any compatible AI client, using this same EXE.
- `/reconnect`: retry the saved connection to the Kintler service after a network problem. This does not approve a device. Normally Controller connects automatically on startup.
- `/shortcut`, `/disconnect`, `/cancel`, `/clear`, `/exit`: local application controls.

Ctrl+P opens all command suggestions without a result limit, Ctrl+D lists devices, arrows scroll the entire suggestion list,
Tab completes a command, Ctrl+C cancels operations, Ctrl+Q exits. Connection requests appear
at the bottom right. Commands and results stay in the main transcript instead of tool panels.
Approval codes are not written into that transcript or stored in command history.
Ctrl+V, Ctrl+Shift+V or Shift+Insert paste text from the Windows system clipboard into the focused field without executing it. Ctrl+A selects that field's contents. The command input is single-line; use one-line commands with explicit separators for scripts.

Commands entered for the selected device are explicit authorization; no additional command-confirmation popup is shown. Device connection approval by its fresh code remains mandatory. Switching devices
cancels the Controller's in-progress work; commands are never redirected to the new target.
Registry/service/task inspection can run as User when OS ACL permits it. Changes still need
Admin/root; the software does not silently elevate itself or bypass permissions.

## Agent

Start the configured standalone Agent. A large six-digit code fills the centre of the terminal.
Before owner confirmation the device cannot accept commands. When connected, the code disappears
and actions appear in order, without row numbers, operator/AI labels or a transcript heading.
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
The old `/connect` spelling remains accepted as an alias of `/reconnect`, but is not in suggestions.
Input fields and buttons use continuous coloured line frames; the prompt keeps its full-block accent strip. Fractional-block borders unsupported by some Windows console fonts are not used.

Source-only Python/Linux users can run the compatible terminal frontend with the `desktop`
extra installed. The published Windows Agent is still native Rust.

# Third-party software

KintlerAgent.exe statically includes the core and Windows adapter from
[vercel-labs/agent-desktop](https://github.com/vercel-labs/agent-desktop),
revision `9eb9b296192fcb70bda25e918cd6dd66abfa3d0d`, version 1.0.2.
Attribution: the vercel-labs/agent-desktop project and its contributors. Licensed under Apache License 2.0.
The complete upstream license is supplied as `licenses/agent-desktop.txt`
in the universal bundle and `docs/AGENT_DESKTOP_LICENSE.txt` in source.

The upstream code is used as an unmodified Cargo dependency pinned to an exact revision.
Kintler supplies its own bounded JSON transport, session scoping, authorization,
process lifecycle and supported-action dispatch around the upstream library.
The upstream CLI and helper executables are not distributed or installed.
Other Cargo/Python dependencies retain their respective licenses.

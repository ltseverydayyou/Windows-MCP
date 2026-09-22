# Windows-MCP

A Windows-focused Model Context Protocol (MCP) server for ChatGPT/OpenAI. It exposes controlled filesystem access, PowerShell, process/system inspection, screenshots, native mouse/keyboard input, window management, Windows UI Automation, clipboard access, installed-app/service discovery, and file hashing.

The server runs locally over **stdio** and can be connected to supported OpenAI products through **Secure MCP Tunnel**, so the Windows machine does not need a public inbound MCP endpoint.

> [!WARNING]
> This MCP can modify files, execute PowerShell, control the desktop, type into applications, terminate processes, and interact with UI controls. Review the source and `config.json` before connecting it to any model.

## Features

- Filesystem: list/stat/read/search/write/replace/move/delete/hash
- PowerShell execution with configurable timeout/output limits
- Process start/stop/list/details and system resource snapshots
- Monitor discovery and screenshots
- Window discovery, focus/state control, and move/resize
- Native mouse, scroll, drag, hotkey, and Unicode text input
- Windows UI Automation inspection/search/read/action tools
- Clipboard read/write
- Installed application and Windows service discovery
- Configurable allowed filesystem roots and feature guards

## Requirements

- Windows 10/11
- Python 3.10+
- OpenAI `tunnel-client` for Secure MCP Tunnel
- A supported OpenAI/ChatGPT workspace or product with MCP/tunnel access
- A Secure MCP Tunnel `tunnel_id`
- A tunnel runtime API key with the required tunnel permissions

Official OpenAI references:

- Secure MCP Tunnel: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- tunnel-client: https://github.com/openai/tunnel-client
- ChatGPT developer mode / MCP apps: https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

## 1. Clone and install

```powershell
git clone https://github.com/ltseverydayyou/Windows-MCP.git
cd Windows-MCP
.\install.cmd
```

`install.cmd` creates `.venv`, upgrades pip, and installs:

- `mcp[cli]`
- `psutil`
- `Pillow`
- `pywinauto`

You can test the stdio server directly with:

```powershell
.\test-server.cmd
```

## 2. Install OpenAI tunnel-client

Download/install the current `tunnel-client` from OpenAI's Platform tunnel settings or the official `openai/tunnel-client` releases.

The PowerShell helpers look for:

1. `tunnel-client` on `PATH`
2. `%LOCALAPPDATA%\OpenAI\TunnelClient\tunnel-client.exe`

If you install it elsewhere, edit `$TunnelClient` in `configure-tunnel.ps1` and `start-tunnel.ps1`.

Check the client first:

```powershell
tunnel-client --version
tunnel-client help quickstart
```

## 3. Create an OpenAI Secure MCP Tunnel

In OpenAI Platform tunnel settings, create/manage a tunnel and obtain its ID:

```text
tunnel_0123456789abcdef0123456789abcdef
```

You also need a **runtime API key for tunnel-client**. Treat this as a secret. Do not commit it to this repository or put it directly into scripts.

Secure MCP Tunnel is outbound-only: `tunnel-client` connects from your Windows machine to OpenAI over HTTPS and forwards MCP work to this local stdio server.

## 4. Save the runtime API key

Recommended on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\save-api-key.ps1
```

The script prompts for the runtime API key as a `SecureString` and stores the encrypted result in:

```text
.control-plane-key
```

The file is protected using Windows DPAPI for the current Windows user and is ignored by Git.

Alternatively, set the key only for the current PowerShell session:

```powershell
$env:CONTROL_PLANE_API_KEY = "YOUR_RUNTIME_KEY"
```

Do **not** add the real key to source control.

## 5. Configure the tunnel profile

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\configure-tunnel.ps1 -TunnelId tunnel_YOUR_ID
```

The helper:

- loads the runtime API key
- configures a `pc-mcp` tunnel-client profile
- uses `sample_mcp_stdio_local`
- points the MCP command at this project's `.venv\Scripts\python.exe server.py`
- configures the local tunnel-client listener
- runs `tunnel-client doctor --profile pc-mcp --explain`

The resulting tunnel-client profile is stored by tunnel-client in your Windows application-data directory.

## 6. Start the tunnel

```powershell
powershell -ExecutionPolicy Bypass -File .\start-tunnel.ps1
```

Leave the tunnel-client running while ChatGPT or another supported OpenAI product is using the MCP.

To restart it:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart-tunnel.ps1
```

You can also validate the profile manually:

```powershell
tunnel-client doctor --profile pc-mcp --explain
```

## 7. Connect it to ChatGPT

According to OpenAI's current Secure MCP Tunnel flow:

1. Enable/use ChatGPT developer mode where your plan/workspace supports it.
2. Open **Plugins** in ChatGPT.
3. Use the **+** control to create a developer-mode app.
4. Choose **Tunnel** as the connection type.
5. Select the available tunnel or paste your `tunnel_id`.
6. Finish creating the app and allow ChatGPT to discover the MCP tools.

If you add, remove, or rename MCP tools in `server.py`, restart the local tunnel and rescan/reconnect the app so ChatGPT receives the updated tool schema.

OpenAI product availability and workspace permissions can change, so use the official links above as the source of truth for current access requirements.

## Configuration

Default `config.json`:

```json
{
  "allowed_roots": [
    "%USERPROFILE%"
  ],
  "allow_all_filesystem": false,
  "shell_enabled": true,
  "shell_timeout_seconds": 120,
  "max_file_bytes": 2097152,
  "max_output_chars": 120000,
  "desktop_control_enabled": true,
  "ui_automation_enabled": true,
  "max_screenshot_pixels": 20000000
}
```

### Filesystem scope

By default, filesystem operations are restricted to `%USERPROFILE%`.

To add another permitted root:

```json
{
  "allowed_roots": [
    "%USERPROFILE%",
    "D:\\Projects"
  ]
}
```

Setting `allow_all_filesystem` to `true` removes that path boundary. Only do this if you explicitly want the MCP to access the whole filesystem.

### Disabling high-impact capabilities

Disable PowerShell:

```json
"shell_enabled": false
```

Disable native desktop control:

```json
"desktop_control_enabled": false
```

Disable Windows UI Automation:

```json
"ui_automation_enabled": false
```

## Security notes

- Never commit `.control-plane-key`, API keys, environment dumps, or tunnel credentials.
- Keep `allowed_roots` narrow.
- Review requested write/shell/UI actions before approving them.
- The MCP's tool annotations distinguish read operations from consequential write/process/shell actions, but annotations are not a sandbox.
- Secure MCP Tunnel avoids exposing the local MCP server through a public inbound port; it does not remove the privileges of the local MCP process itself.
- Run the server as a normal Windows user unless elevated access is specifically required.

## Project layout

```text
Windows-MCP/
├─ server.py
├─ config.json
├─ requirements.txt
├─ install.cmd
├─ test-server.cmd
├─ save-api-key.ps1
├─ configure-tunnel.ps1
├─ start-tunnel.ps1
├─ restart-tunnel.ps1
├─ SECURITY.md
└─ .gitignore
```

## Updating

```powershell
git pull
.\install.cmd
powershell -ExecutionPolicy Bypass -File .\restart-tunnel.ps1
```

After a tool-schema change, reconnect/rescan the app in ChatGPT.

## Notes

The published repository intentionally excludes local runtime state such as `.venv`, `__pycache__`, and `.control-plane-key`.

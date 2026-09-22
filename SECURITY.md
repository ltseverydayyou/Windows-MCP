# Security

Windows-MCP intentionally exposes powerful local capabilities. Treat the MCP server as privileged automation running with the permissions of the Windows account that launches it.

## Secrets

Never commit or share:

- `.control-plane-key`
- `CONTROL_PLANE_API_KEY`
- OpenAI runtime/API keys
- tunnel-client credentials or private profile material
- copied environment dumps containing tokens

The supplied `save-api-key.ps1` stores the tunnel runtime key using Windows DPAPI for the current Windows user.

## Reduce access

Keep `allow_all_filesystem` disabled unless full filesystem access is explicitly required. Prefer a small `allowed_roots` list.

Disable unused capabilities in `config.json`:

- `shell_enabled`
- `desktop_control_enabled`
- `ui_automation_enabled`

Run the MCP as a standard user rather than Administrator unless elevated access is required for a specific task.

## Consequential tools

File writes/deletes, PowerShell, process termination/start, keyboard/mouse input, clipboard writes, window changes, and UI Automation actions can change local state. Review such actions carefully.

## Reporting

If you report a bug or security issue through GitHub, remove credentials, personal paths, account identifiers, and other secrets from logs/reproduction data.

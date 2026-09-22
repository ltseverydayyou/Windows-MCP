param(
    [string]$TunnelId
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$TunnelClient = "C:\Users\PC-Admin\AppData\Local\OpenAI\TunnelClient\tunnel-client.exe"
$PythonExe = Join-Path $Base ".venv\Scripts\python.exe"
$ServerPy = Join-Path $Base "server.py"
$KeyFile = Join-Path $Base ".control-plane-key"

if (-not (Test-Path $TunnelClient)) {
    throw "tunnel-client.exe not found at: $TunnelClient"
}
if (-not (Test-Path $PythonExe)) {
    throw "Python venv not found at: $PythonExe. Run install.cmd first."
}
if (-not (Test-Path $ServerPy)) {
    throw "server.py not found at: $ServerPy"
}
if (-not $TunnelId) {
    $TunnelId = Read-Host "Paste your OpenAI tunnel_id"
}
if (-not $TunnelId.StartsWith("tunnel_")) {
    throw "That does not look like a tunnel_id."
}

if ($env:CONTROL_PLANE_API_KEY) {
    $ApiKey = $env:CONTROL_PLANE_API_KEY
} elseif (Test-Path $KeyFile) {
    $Encrypted = [System.IO.File]::ReadAllText($KeyFile, [System.Text.Encoding]::UTF8)
    $Secure = ConvertTo-SecureString $Encrypted
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
    }
} else {
    throw "No API key found. Run save-api-key.ps1 first or set CONTROL_PLANE_API_KEY."
}

# tunnel-client parses --mcp-command using shell-style escaping.
# Convert Windows backslashes to forward slashes so paths survive parsing.
$PythonCmdPath = $PythonExe.Replace('\', '/')
$ServerCmdPath = $ServerPy.Replace('\', '/')
$McpCommand = "`"$PythonCmdPath`" `"$ServerCmdPath`""

Write-Host "MCP executable: $PythonCmdPath"
Write-Host "MCP server:     $ServerCmdPath"
Write-Host "MCP command:    $McpCommand"
Write-Host ""

$env:CONTROL_PLANE_API_KEY = $ApiKey

try {
    & $TunnelClient init `
        --sample sample_mcp_stdio_local `
        --profile pc-mcp `
        --tunnel-id $TunnelId `
        --mcp-command $McpCommand

    if ($LASTEXITCODE -ne 0) {
        throw "tunnel-client init failed with exit code $LASTEXITCODE"
    }

    
    $ProfilePath = Join-Path $env:APPDATA "tunnel-client\pc-mcp.yaml"
    if (-not (Test-Path $ProfilePath)) {
        throw "Tunnel profile was not created at: $ProfilePath"
    }
    $ProfileText = [System.IO.File]::ReadAllText($ProfilePath, [System.Text.Encoding]::UTF8)
    $ProfileText = $ProfileText -replace '(?m)^(\s*listen_addr:\s*)"[^"]*"\s*$', '$1"localhost:8080"'
    [System.IO.File]::WriteAllText($ProfilePath, $ProfileText, (New-Object System.Text.UTF8Encoding($false)))
& $TunnelClient doctor --profile pc-mcp --explain
    if ($LASTEXITCODE -ne 0) {
        throw "tunnel-client doctor failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    $ApiKey = $null
}

Write-Host ""
Write-Host "Tunnel profile 'pc-mcp' configured and validated."
Write-Host "Next: run start-tunnel.ps1"
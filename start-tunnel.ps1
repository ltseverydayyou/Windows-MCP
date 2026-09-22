$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$TunnelClient = "C:\Users\PC-Admin\AppData\Local\OpenAI\TunnelClient\tunnel-client.exe"
$KeyFile = Join-Path $Base ".control-plane-key"

if (-not (Test-Path $TunnelClient)) {
    throw "tunnel-client.exe not found at: $TunnelClient"
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

$env:CONTROL_PLANE_API_KEY = $ApiKey

# Stop an older pc-mcp tunnel instance first so its health listener on localhost:8080
# does not make `tunnel-client doctor` fail when this launcher is run again.
$ExistingTunnelProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -ieq "tunnel-client.exe" -and
        $_.CommandLine -match '(?i)\brun\s+--profile\s+pc-mcp\b'
    }

foreach ($Existing in $ExistingTunnelProcesses) {
    Write-Host "Stopping existing pc-mcp tunnel process PID $($Existing.ProcessId)..."
    Stop-Process -Id $Existing.ProcessId -Force -ErrorAction SilentlyContinue
}

if ($ExistingTunnelProcesses) {
    $Deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $Listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    } while ($Listener -and (Get-Date) -lt $Deadline)

    if ($Listener) {
        throw "Port localhost:8080 is still occupied after stopping the previous pc-mcp tunnel."
    }
}

try {
    & $TunnelClient doctor --profile pc-mcp --explain
    if ($LASTEXITCODE -ne 0) {
        throw "tunnel-client doctor failed with exit code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Starting PC MCP tunnel. Leave this window open."
    & $TunnelClient run --profile pc-mcp
    if ($LASTEXITCODE -ne 0) {
        throw "tunnel-client run failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    $ApiKey = $null
}
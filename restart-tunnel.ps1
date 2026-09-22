$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path

$Current = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "tunnel-client.exe" -and $_.CommandLine -match "run --profile pc-mcp"
}

foreach ($Process in $Current) {
    try {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not stop tunnel-client PID $($Process.ProcessId): $($_.Exception.Message)"
    }
}

Start-Sleep -Milliseconds 750
& (Join-Path $Base "start-tunnel.ps1")
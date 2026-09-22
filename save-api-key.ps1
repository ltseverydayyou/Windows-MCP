$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutFile = Join-Path $Base ".control-plane-key"

$Secure = Read-Host "Paste the OpenAI runtime API key for tunnel-client" -AsSecureString
$Encrypted = ConvertFrom-SecureString -SecureString $Secure
[System.IO.File]::WriteAllText($OutFile, $Encrypted, [System.Text.UTF8Encoding]::new($false))

Write-Host "Saved encrypted API key to $OutFile"
Write-Host "It is protected with Windows DPAPI for your current Windows user."
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Venv = "$env:USERPROFILE\.venvs\expra-connect"
)
$ErrorActionPreference = "Stop"
$base = $env:EXPRA_CONNECT_RELEASE_BASE_URL
if ([string]::IsNullOrWhiteSpace($base)) {
    $base = "https://github.com/20204166/expra-connect/releases/download/v$Version"
}
$work = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $wheel = "expra_connect-$Version-py3-none-any.whl"
    Invoke-WebRequest "$base/$wheel" -OutFile "$work/$wheel"
    Invoke-WebRequest "$base/SHA256SUMS" -OutFile "$work/SHA256SUMS"
    $expected = (Get-Content "$work/SHA256SUMS" | Where-Object { $_ -match [regex]::Escape($wheel) }).Split()[0]
    $actual = (Get-FileHash "$work/$wheel" -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected.ToLowerInvariant() -ne $actual) { throw "wheel checksum mismatch" }
    py -m venv $Venv
    & "$Venv\Scripts\python.exe" -m pip install --upgrade "$work/$wheel"
    $installed = & "$Venv\Scripts\python.exe" -c "from expra_connect import __version__; print(__version__)"
    if ($installed.Trim() -ne $Version) { throw "installed version mismatch" }
    Write-Output "Installed expra-peer in $Venv"
} finally {
    Remove-Item -Recurse -Force $work
}

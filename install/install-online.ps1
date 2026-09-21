# Standalone online installer. Run from any folder:
# irm https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1 | iex
param([switch]$System)
$ErrorActionPreference = "Stop"
$base = "https://raw.githubusercontent.com/20204166/expra-connect/main/dist"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("expra-connect-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $sumsPath = Join-Path $tmp "SHA256SUMS"
    $wheelPath = $null
    $actual = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $cacheBust = [guid]::NewGuid().ToString()
        Invoke-WebRequest "$base/SHA256SUMS?cache=$cacheBust" -OutFile $sumsPath
        $line = Get-Content $sumsPath | Where-Object { $_.Trim() } | Select-Object -Last 1
        $parts = $line -split "\s+"
        $expected = $parts[0]
        $wheel = $parts[1]
        if ($wheel -notmatch "^expra_connect-(\d+\.\d+\.\d+\.\d+)-py3-none-any\.whl$") {
            throw "Unexpected wheel filename: $wheel"
        }
        $expectedVersion = $matches[1]
        $wheelPath = Join-Path $tmp $wheel
        Invoke-WebRequest "$base/$wheel?cache=$cacheBust" -OutFile $wheelPath
        $actual = (Get-FileHash $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -eq $expected.ToLowerInvariant()) { break }
        if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
    }
    if ($actual -ne $expected.ToLowerInvariant()) { throw "Wheel checksum mismatch" }

    function Test-VenvPython {
        param([object]$Py)
        & $Py -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    if (-not (Get-Command "py" -ErrorAction SilentlyContinue) -and
        -not (Get-Command "python" -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
            throw "Python is not installed and winget is unavailable. Install Python 3.10 or newer, then retry."
        }
        Write-Host "Python was not found. Installing Python 3.12 with winget..."
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget could not install Python 3.12 (exit $LASTEXITCODE)" }
    }
    function Get-PythonCandidates {
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($m in @("3.14", "3.13", "3.12", "3.11", "3.10")) {
            if (Get-Command "py" -ErrorAction SilentlyContinue) { $candidates.Add(@("py", "-$m")) }
        }
        if (Get-Command "py" -ErrorAction SilentlyContinue) { $candidates.Add(@("py", "-3")) }
        foreach ($name in @("python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3", "python")) {
            if (Get-Command $name -ErrorAction SilentlyContinue) { $candidates.Add(@($name)) }
        }
        foreach ($path in @(
            "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
        )) { if (Test-Path $path) { $candidates.Add(@($path)) } }
        return $candidates
    }

    $py = $null
    $basePy = $null
    $reasons = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in Get-PythonCandidates) {
        try {
            & $candidate -c "import sys; assert sys.version_info >= (3,10)" 2>$null
            if ($LASTEXITCODE -ne 0) { $reasons.Add("'$($candidate -join ' ')' is not Python 3.10+"); continue }
            if (-not (Test-VenvPython $candidate)) { $py = $candidate; break }
            $reasons.Add("'$($candidate -join ' ')' is inside a virtual environment")
            if (-not $basePy) { $basePy = $candidate }
        } catch { $reasons.Add("'$($candidate -join ' ')' could not be launched") }
    }
    if (-not $py -and $basePy) {
        $candidateBase = & $basePy -c "import sys; print(sys._base_executable)" 2>$null
        if ($candidateBase -and (Test-Path $candidateBase) -and -not (Test-VenvPython $candidateBase)) { $py = @($candidateBase) }
    }
    if (-not $py) {
        Write-Host "No usable Python 3.10+ interpreter was found."
        $reasons | ForEach-Object { Write-Host "  - $_" }
        throw "No usable Python 3.10+ interpreter was found after bootstrap."
    }
    if (Test-VenvPython $py) { throw "Cannot install into an active virtual environment." }
    if ($env:VIRTUAL_ENV) { Write-Warning "Active virtual environment '$env:VIRTUAL_ENV' was not modified." }

    $pipArgs = @("-m", "pip", "install", "--force-reinstall")
    if (-not $System) { $pipArgs += "--user" }
    $pipArgs += @("--break-system-packages", $wheelPath)
    & $py @pipArgs
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

    $verify = @'
import importlib.metadata as metadata
import sys
expected = sys.argv[1]
actual = metadata.version("expra-connect")
if actual != expected:
    raise SystemExit(f"installed version {actual} does not match wheel version {expected}")
import expra_connect
print(f"Installed expra-connect {actual}")
print(f"  package: {expra_connect.__file__}")
'@
    $verifyPath = Join-Path $tmp "verify_installed.py"
    [System.IO.File]::WriteAllText($verifyPath, $verify)
    & $py $verifyPath $expectedVersion
    if ($LASTEXITCODE -ne 0) { throw "installed wheel verification failed" }

    $scheme = if ($env:OS -eq "Windows_NT") { if ($System) { "nt" } else { "nt_user" } } else { if ($System) { "posix_prefix" } else { "posix_user" } }
    $bin = & $py -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='$scheme'))"
    if ($env:OS -eq "Windows_NT" -and $bin -and (Test-Path $bin)) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $already = ($userPath -split ";") | Where-Object { $_.TrimEnd("\") -eq $bin.TrimEnd("\") }
        if (-not $already) { [Environment]::SetEnvironmentVariable("Path", "$bin;$userPath", "User") }
        if (-not (($env:Path -split ";") | Where-Object { $_.TrimEnd("\") -eq $bin.TrimEnd("\") })) { $env:Path = "$bin;$env:Path" }
    }
    Write-Host "Installed $wheel. Console scripts: $bin"
    Write-Host "Run: expra-peer --version"
} finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }

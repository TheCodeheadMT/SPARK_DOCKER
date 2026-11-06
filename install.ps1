# SPARK installer for Windows (PowerShell 5.1 or 7).
#
#   irm https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.ps1 | iex
#
# Needs Docker Desktop (or Rancher Desktop / Podman Desktop with Docker Compose).
# Installs into %USERPROFILE%\spark, starts SPARK and opens it in the browser, and adds
# a `spark` command. Running it again updates an existing install and keeps your data.
#
# Options (set before running, e.g.  $env:SPARK_PORT = "8080"):
#   SPARK_HOME     install folder                (default %USERPROFILE%\spark)
#   SPARK_PORT     port for http://localhost:... (default: 8000, or the next free one)
#   SPARK_REF      version to run: main, a tag like v0.1.0-rc1, or a commit (default main)
#   SPARK_NO_OPEN  set to 1 to not open the browser
#
# Everything is inside a function so nothing runs if the download is cut short, and
# errors end the installer without closing your PowerShell window.

function Install-Spark {
    # 'Continue', not 'Stop': in Windows PowerShell 5.1 'Stop' turns any stderr output from docker into an error.
    # Failures are checked explicitly instead.
    $ErrorActionPreference = 'Continue'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Raw = if ($env:SPARK_RAW_BASE) { $env:SPARK_RAW_BASE } else { 'https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main' }
    $HomeDir = if ($env:SPARK_HOME) { $env:SPARK_HOME } else { Join-Path $env:USERPROFILE 'spark' }
    $Container = 'spark'

    function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
    function Ok($m)   { Write-Host "  + $m" -ForegroundColor Green }
    function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
    function Fail($m) { Write-Host "  x $m" -ForegroundColor Red; throw 'SPARK installation stopped.' }
    function DockerOk { & docker info *> $null; return ($LASTEXITCODE -eq 0) }
    function PortFree([int]$p) {
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect('127.0.0.1', $p); return $false } catch { return $true } finally { $c.Close() }
    }
    function WriteUtf8([string]$path, [string]$text) {
        # UTF-8 without BOM and LF endings, so Docker Compose reads the file correctly
        [IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
    }

    Write-Host ''
    Write-Host 'SPARK installer' -ForegroundColor White
    Write-Host ''

    # --- 1. Docker ------------------------------------------------------------------------
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail 'Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ , start it once, then run this command again.'
    }
    if (-not (DockerOk)) {
        Say 'Starting Docker Desktop...'
        $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (Test-Path $dd) { Start-Process $dd | Out-Null }
        $i = 0
        while (-not (DockerOk)) {
            $i++
            if ($i -gt 60) { Fail 'Docker is not running. Start Docker Desktop, wait until it says it is running, then run this command again.' }
            Start-Sleep -Seconds 2
        }
    }
    Ok 'Docker is running'

    $cv = (& docker compose version --short 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $cv) { Fail 'Docker Compose v2 is missing. Update Docker Desktop.' }
    $cv = "$cv".Trim().TrimStart('v')
    $parts = $cv.Split('.')
    if ($parts.Count -ge 2 -and $parts[0] -match '^\d+$' -and $parts[1] -match '^\d+$') {
        if ([int]$parts[0] -lt 2 -or ([int]$parts[0] -eq 2 -and [int]$parts[1] -lt 17)) { Fail "Docker Compose $cv is too old (2.17 or newer needed). Update Docker Desktop." }
    }
    Ok "Docker Compose $cv"

    # --- 2. Files -------------------------------------------------------------------------
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
    Push-Location $HomeDir
    Say "Installing to $HomeDir"
    try {
        Invoke-WebRequest -UseBasicParsing "$Raw/deploy/local/docker-compose.yml" -OutFile 'docker-compose.yml.new' -ErrorAction Stop
        Invoke-WebRequest -UseBasicParsing "$Raw/deploy/local/.env.example" -OutFile '.env.example' -ErrorAction Stop
    } catch { Fail "Could not download SPARK. Check the internet connection. ($($_.Exception.Message))" }
    Move-Item -Force 'docker-compose.yml.new' 'docker-compose.yml'

    $envFile = Join-Path $HomeDir '.env'
    function SetEnv([string]$key, [string]$value) {
        $lines = @(Get-Content $envFile)
        $done = $false
        $out = foreach ($l in $lines) {
            if (-not $done -and $l -match "^\s*#?\s*$key=") { "$key=$value"; $done = $true } else { $l }
        }
        if (-not $done) { $out = @($out) + "$key=$value" }
        WriteUtf8 $envFile (($out -join "`n") + "`n")
    }
    function GetEnv([string]$key) {
        foreach ($l in (Get-Content $envFile)) { if ($l -match "^$key=([^\s#]*)") { return $Matches[1] } }
        return $null
    }

    $running = [bool](& docker ps -q --filter "name=^/$Container$")
    if (-not (Test-Path $envFile)) {
        WriteUtf8 $envFile ([IO.File]::ReadAllText((Join-Path $HomeDir '.env.example')))
        $port = $env:SPARK_PORT
        if (-not $port) {
            $port = 8000
            while (-not (PortFree $port)) {
                $port++
                if ($port -gt 8100) { Fail 'No free port between 8000 and 8100. Set one with $env:SPARK_PORT = "..."' }
            }
        }
        SetEnv 'SPARK_PORT' "$port"
        Ok "Settings created (.env), port $port"
    } else {
        if ($env:SPARK_PORT) { SetEnv 'SPARK_PORT' $env:SPARK_PORT }
        Ok 'Keeping your settings (.env)'
    }
    if ($env:SPARK_REF) { SetEnv 'SPARK_REF' $env:SPARK_REF }
    $port = GetEnv 'SPARK_PORT'; if (-not $port) { $port = 8000 }
    if (-not $running -and -not (PortFree ([int]$port))) {
        Fail "Port $port is in use by another program. Set SPARK_PORT=<port> in $envFile, then run this again."
    }

    # --- 3. The 'spark' command -------------------------------------------------------------
    $ctl = @'
# SPARK control command (created by the installer). Run: spark help
param([string]$Command = 'help')
$HomeDir = '__HOME__'
Set-Location $HomeDir
$port = 8000
foreach ($l in (Get-Content (Join-Path $HomeDir '.env') -ErrorAction SilentlyContinue)) { if ($l -match '^SPARK_PORT=(\d+)') { $port = $Matches[1] } }
$url = "http://localhost:$port"
switch ($Command) {
    'start'     { docker compose up -d; Write-Host "SPARK is starting: $url (first start can take a few minutes; see: spark logs)" }
    'stop'      { docker compose down }
    'restart'   { docker compose restart; Write-Host "Restarted (and updated): $url" }
    'update'    { $env:SPARK_HOME = $HomeDir; Invoke-Expression (Invoke-RestMethod '__RAW__/install.ps1') }
    'logs'      { docker compose logs -f --tail 100 }
    'status'    { docker compose ps; Write-Host $url }
    'open'      { Start-Process $url }
    'uninstall' {
        docker compose down -v --rmi all
        $p = [Environment]::GetEnvironmentVariable('Path', 'User')
        [Environment]::SetEnvironmentVariable('Path', (($p -split ';') | Where-Object { $_ -and $_ -ne $HomeDir }) -join ';', 'User')
        Write-Host "SPARK removed. Your data is still in $HomeDir\spark-data; delete $HomeDir to remove everything."
    }
    default {
        Write-Host @"
Usage: spark <command>
  start      start SPARK
  stop       stop SPARK
  restart    restart and update to the latest version
  update     re-run the installer (also updates this command)
  logs       follow the log (Ctrl+C to stop)
  status     show whether SPARK is running
  open       open SPARK in the browser
  uninstall  remove SPARK (keeps your data folder)
"@
    }
}
'@
    WriteUtf8 (Join-Path $HomeDir 'spark.ps1') ($ctl.Replace('__HOME__', $HomeDir.Replace("'", "''")).Replace('__RAW__', $Raw))
    # spark.cmd lets `spark` work from cmd and PowerShell regardless of the script execution policy
    [IO.File]::WriteAllText((Join-Path $HomeDir 'spark.cmd'),
        "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0spark.ps1`" %*`r`n")
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains $HomeDir)) {
        [Environment]::SetEnvironmentVariable('Path', ((@($userPath -split ';' | Where-Object { $_ }) + $HomeDir) -join ';'), 'User')
        $env:Path = "$env:Path;$HomeDir"
    }

    # --- 4. Start ---------------------------------------------------------------------------
    Say 'Starting SPARK (the first start downloads and builds everything: about 3-5 minutes)'
    $log = Join-Path $HomeDir 'compose.log'
    $p = Start-Process docker -ArgumentList 'compose', 'up', '-d', '--build', '--force-recreate', '--quiet-pull' `
        -NoNewWindow -Wait -PassThru -RedirectStandardError $log -RedirectStandardOutput (Join-Path $HomeDir 'compose.out')
    if ($p.ExitCode -ne 0) { Get-Content $log | Write-Host; Fail 'Docker could not start SPARK (see the messages above).' }
    Remove-Item -Force $log, (Join-Path $HomeDir 'compose.out') -ErrorAction SilentlyContinue

    $url = "http://localhost:$port"
    $shown = 0; $t = 0
    while ($true) {
        $lines = @((& docker logs $Container 2>&1) | ForEach-Object { "$_" } | Where-Object { $_.StartsWith('[spark]') })
        if ($lines.Count -gt $shown) {
            $lines[$shown..($lines.Count - 1)] | ForEach-Object { Write-Host ('   ' + $_.Substring(7).Trim()) }
            $shown = $lines.Count
        }
        try { Invoke-WebRequest -UseBasicParsing "$url/api/health" -TimeoutSec 3 -ErrorAction Stop | Out-Null; break } catch { }
        $state = (& docker inspect -f '{{.State.Status}}' $Container 2>$null)
        if ($state -ne 'running' -and $state -ne 'created') {
            & docker logs --tail 30 $Container 2>&1 | ForEach-Object { Write-Host "$_" }
            Fail 'SPARK stopped while starting (see the log above).'
        }
        $t += 3
        if ($t -gt 1200) { Fail 'SPARK is taking unusually long to start. Check: spark logs' }
        Start-Sleep -Seconds 3
    }

    Ok 'SPARK is running'
    if ($env:SPARK_NO_OPEN -ne '1') { Start-Process $url }

    Write-Host ''
    Write-Host "SPARK is ready: $url" -ForegroundColor White
    Write-Host ''
    Write-Host "  Your data:   $HomeDir\spark-data   (back up this folder)"
    Write-Host "  Settings:    $HomeDir\.env"
    Write-Host ''
    Write-Host '  spark stop        stop SPARK'
    Write-Host '  spark start       start it again (it also starts with Docker Desktop)'
    Write-Host '  spark restart     restart and update to the latest version'
    Write-Host '  spark help        all commands'
    Write-Host '  (open a new terminal window for the spark command to be available)'
    Write-Host ''
}

$sparkStartDir = Get-Location
try { Install-Spark } catch { if ($_.Exception.Message -ne 'SPARK installation stopped.') { Write-Host "  x $($_.Exception.Message)" -ForegroundColor Red } }
finally { Set-Location $sparkStartDir }

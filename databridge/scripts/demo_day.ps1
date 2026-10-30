<#
  Walnut DataBridge: the one-click Demo Day kit (docs/finals/demo_day/DEMO_DAY_RUNBOOK.md).

  The four .cmd files at the repo root call this script; double-click them, do not run this by hand.

    demo_day.ps1 start      [-NoWarm] [-NoBrowser] [-Windowed] [-Rebuild] [-Browser edge|chrome] [-DryRun]
    demo_day.ps1 stop       [-DryRun]
    demo_day.ps1 reset      [-Fresh] [-NoBrowser] [-Windowed] [-DryRun]
    demo_day.ps1 offline    [-NoBrowser] [-Windowed] [-Rebuild] [-DryRun]
    demo_day.ps1 status
    demo_day.ps1 shortcuts  [-Target <folder>] [-DryRun]

  start    checks prerequisites, starts the stack with scripts/demo_up.sh (Git Bash; it auto-detects
           Hy3), waits for health, runs the preflight with one real ask per scripted question
           (scripts/demo_preflight.py --warm), opens a clean browser at /present?present=1, prints READY.
           Safe to run twice: running services are reused, the demo browser is reopened clean.
  stop     closes the demo browser and stops every demo service (3100, 8100, 8200, 8300, 3300, 3101).
  reset    known clean state: Simulator reset (admin.it), all demo sessions signed out (the browser
           profile is wiped and reopened), the scripted questions warmed again. -Fresh also wipes and
           reseeds the demo history (about 3 minutes).
  offline  rung 3 of the ladder: the mock-mode build on :3101 (fixtures, no services, no network),
           same presenter path.
  shortcuts  puts the four buttons on the Desktop (or -Target).

  Windows PowerShell 5.1 compatible. ASCII only on purpose (5.1 reads BOM-less files as ANSI).
#>
param(
  [Parameter(Position = 0)][ValidateSet('start', 'stop', 'reset', 'offline', 'status', 'shortcuts')][string]$Action = 'start',
  [switch]$Fresh,
  [switch]$DryRun,
  [switch]$NoWarm,
  [switch]$NoBrowser,
  [switch]$Windowed,
  [switch]$Rebuild,
  [ValidateSet('auto', 'edge', 'chrome')][string]$Browser = 'auto',
  [string]$Target = ''
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

# ------------------------------------------------------------------------------------------ paths
$Db = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path            # ...\Tencent\databridge
$Repo = Split-Path $Db -Parent                                     # ...\Tencent
$Web = Join-Path $Db 'apps\internal_brain_web'
$Py = Join-Path $Db '.venv\Scripts\python.exe'
$DayDir = Join-Path $Db 'var\demo-day'
$LogDir = Join-Path $DayDir 'logs'
$BrowserProfile = Join-Path $Db 'var\demo-browser'
$Report = Join-Path $DayDir 'preflight.json'
$LiveUrl = 'http://127.0.0.1:3100'
$MockUrl = 'http://127.0.0.1:3101'
$PresentPath = '/present?present=1'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# ---------------------------------------------------------------------------------------- output
function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }
function Head([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "  [ OK ] $Text" -ForegroundColor Green }
function Warn([string]$Text) { Write-Host "  [WARN] $Text" -ForegroundColor Yellow }
function Bad([string]$Text) { Write-Host "  [FAIL] $Text" -ForegroundColor Red }
function Dry([string]$Text) { Write-Host "  [DRY RUN] would $Text" -ForegroundColor Magenta }

function Show-Big([string[]]$Lines, [string]$Color) {
  Write-Host ''
  foreach ($l in $Lines) { Write-Host "    $l" -ForegroundColor $Color }
  Write-Host ''
}
$ReadyArt = @(
  '#####   ######    ####    #####   #     #',
  '#    #  #        #    #   #    #   #   # ',
  '#####   #####    ######   #    #    ###  ',
  '#   #   #        #    #   #    #     #   ',
  '#    #  ######   #    #   #####      #   '
)
$NotReadyArt = @(
  '#   #   ###   #####     #####   ######   ####   #####   #   #',
  '##  #  #   #    #       #    #  #       #    #  #    #   # # ',
  '# # #  #   #    #       #####   #####   ######  #    #    #  ',
  '#  ##  #   #    #       #   #   #       #    #  #    #    #  ',
  '#   #   ###     #       #    #  ######  #    #  #####     #  '
)

function Show-Keys {
  Say '  Presenter keys (in the demo browser window):' 'White'
  Say '    P            start: title card, Stage theme, signs in the personas'
  Say '    Space        next step (a failed step: Space tries again, Right arrow skips it)'
  Say '    Left arrow   back one step'
  Say '    B            play the backup clip for this step; B again returns to live'
  Say '    R            reset (asks to confirm): sign-outs, back to the title card'
  Say '    L            English / Simplified Chinese'
  Say '    S  Stage theme on/off     Esc  hide the bar     ?  key legend'
  Say '    Ctrl+Shift+P disarm the presenter keys     F11  leave full screen'
}

# --------------------------------------------------------------------------------------- probes
function Test-Http([string]$Url, [int]$TimeoutSec = 4) {
  try {
    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $req.Proxy = $null
    $req.Method = 'GET'
    $resp = $req.GetResponse()
    $code = [int]$resp.StatusCode
    $resp.Close()
    return ($code -ge 200 -and $code -lt 400)
  } catch {
    return $false
  }
}

# Round-5 Opus: the passcode the Brain checks can live only in databridge\.env.ib.local. Read that ONE
# key as data (never run as code), the way bash reads a plain assignment: quotes, backslashes, a '#'
# inside a word kept, a ' #' comment dropped, the last assignment wins. As in demo_up.sh, a non-empty
# value in the file wins over the environment. Anything bash would expand ($, backtick, ~, a line
# continuation) is not guessed at: the environment value is used and Source says 'unreadable'.
# The value is never printed. Returns @{ Value = <string or $null>; Source = 'file'|'env'|'none'|'unreadable' }.
function Get-DemoPasscode([string]$Dir = $Db) {
  $fromEnv = [string]$env:IB_DEMO_PASSCODE
  $res = @{ Value = $null; Source = 'none' }
  if ($fromEnv) { $res.Value = $fromEnv; $res.Source = 'env' }
  $file = Join-Path $Dir '.env.ib.local'
  if (-not (Test-Path -LiteralPath $file)) { return $res }
  try { $bytes = [IO.File]::ReadAllBytes($file) } catch { return $res }
  # No BOM stripping and no CR stripping: bash keeps both, so the Brain does too.
  $text = (New-Object Text.UTF8Encoding $false).GetString($bytes)
  $found = $false; $val = ''; $bad = $false
  foreach ($line in $text.Split([char]10)) {
    $s = $line.TrimStart([char]32, [char]9)
    if ($s -match '^export[ \t]+') { $s = $s.Substring($Matches[0].Length) }
    $key = 'IB_DEMO_PASSCODE='
    if (-not $s.StartsWith($key, [StringComparison]::Ordinal)) { continue }
    $w = $s.Substring($key.Length)
    $sb = New-Object Text.StringBuilder
    $i = 0; $n = $w.Length; $ok = $true
    if ($n -gt 0 -and $w[0] -eq '~') { $ok = $false }
    while ($ok -and $i -lt $n) {
      $c = $w[$i]
      if ($c -eq ' ' -or $c -eq "`t" -or $c -eq ';') { break }
      if ('$`&|<>()'.IndexOf($c) -ge 0) { $ok = $false; break }
      if ($c -eq '\') {
        if ($i + 1 -ge $n) { $ok = $false; break }
        [void]$sb.Append($w[$i + 1]); $i += 2; continue
      }
      if ($c -eq "'") {
        $j = $w.IndexOf("'", $i + 1)
        if ($j -lt 0) { $ok = $false; break }
        [void]$sb.Append($w.Substring($i + 1, $j - $i - 1)); $i = $j + 1; continue
      }
      if ($c -eq '"') {
        $i++; $closed = $false
        while ($i -lt $n) {
          $d = $w[$i]
          if ($d -eq '"') { $closed = $true; $i++; break }
          if ($d -eq '$' -or $d -eq '`') { $ok = $false; break }
          if ($d -eq '\' -and $i + 1 -lt $n -and ('$`"\'.IndexOf($w[$i + 1]) -ge 0)) { [void]$sb.Append($w[$i + 1]); $i += 2; continue }
          [void]$sb.Append($d); $i++
        }
        if (-not $closed) { $ok = $false }
        continue
      }
      [void]$sb.Append($c); $i++
    }
    if ($ok) {
      # After the word: only blanks, then nothing, a comment or ';'. Anything else makes the line a
      # command with a one-off variable, which bash does not keep.
      $tail = $w.Substring([Math]::Min($i, $n)).TrimStart([char]32, [char]9)
      if ($tail.Length -gt 0 -and $tail[0] -ne '#' -and $tail[0] -ne ';') { $ok = $false }
    }
    $found = $true; $bad = -not $ok; $val = $(if ($ok) { $sb.ToString() } else { '' })
  }
  if ($found -and $bad) { $res.Source = 'unreadable' }
  elseif ($found -and $val) { $res.Value = $val; $res.Source = 'file' }
  return $res
}

function Test-SignIn {
  # One demo sign-in straight at the Internal Brain (ledgered as a sign-in, like any other).
  try {
    $req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8100/auth/demo-login')
    $req.Method = 'POST'; $req.ContentType = 'application/json'; $req.Proxy = $null; $req.Timeout = 15000
    # ConvertTo-Json escapes a passcode with " or \ (round-4 Opus #10); UTF-8 keeps any non-ASCII intact.
    $payload = @{ persona = 'maya.compliance' }
    $pc = Get-DemoPasscode
    if ($pc.Value) { $payload['passcode'] = [string]$pc.Value }
    $body = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $payload -Compress))
    $s = $req.GetRequestStream(); $s.Write($body, 0, $body.Length); $s.Close()
    $r = $req.GetResponse(); $ok = ([int]$r.StatusCode -eq 200); $r.Close()
    return $ok
  } catch { return $false }
}

function Test-Port([int]$Port) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $t = $c.ConnectAsync('127.0.0.1', $Port)
    return ($t.Wait(600) -and $c.Connected)
  } catch { return $false } finally { $c.Close() }
}

function Wait-Http([string]$Url, [int]$Seconds) {
  for ($i = 0; $i -lt $Seconds; $i++) {
    if (Test-Http $Url 3) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

# --------------------------------------------------------------------- our processes, by port
# demo_up.sh stops services by port. When that misses (it can, e.g. under a sandbox or when a process
# was started from another console) we fall back to the command line. EVERY candidate, whether found
# by its command line or as the owner of the port's socket, must name this repo's own path in its
# command line (demo_up.sh starts Python by its absolute .venv path; Next runs from
# apps\internal_brain_web\node_modules). Anything else on a demo port is reported, never killed.
$DbLower = $Db.ToLower().TrimEnd('\') + '\'
$DbFwd = $DbLower.Replace('\', '/')
function Test-OursCommandLine([string]$CommandLine) {
  if (-not $CommandLine) { return $false }
  $l = $CommandLine.ToLower()
  return ($l.Contains($DbLower) -or $l.Contains($DbFwd))
}

function Get-DemoProcesses([int]$Port) {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='node.exe'" -ErrorAction SilentlyContinue)
  $hits = @()
  foreach ($p in $procs) {
    $c = [string]$p.CommandLine
    if (-not (Test-OursCommandLine $c)) { continue }
    $l = $c.ToLower()
    $mine = $false
    switch ($Port) {
      8200 { $mine = $l.Contains('mock_platforms') -and $l.Contains('--port 8200') }
      8100 { $mine = $l.Contains('-m databridge_ib') }
      8300 { $mine = ($l.Contains('apps/gateway/serve.py') -or $l.Contains('apps\gateway\serve.py')) }
      default { $mine = $l.Contains('next') -and ($l -match "-p $Port(\s|$|`")") -and ($l.Contains(' start') -or $l.Contains('start-server')) }
    }
    if ($mine) { $hits += $p }
  }
  # Owners by socket, when Windows tells us (normal case outside a sandbox): only this repo's own.
  try {
    $owners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($o in $owners) {
      if ($o -and -not ($hits | Where-Object { $_.ProcessId -eq $o })) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$o" -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        if (($p.Name -eq 'python.exe' -or $p.Name -eq 'node.exe') -and (Test-OursCommandLine ([string]$p.CommandLine))) { $hits += $p }
        else { Warn "Port ${Port}: $($p.Name) (PID $o) is not part of this repo; it is left running" }
      }
    }
  } catch { }
  return $hits
}

function Stop-DemoPort([int]$Port) {
  $ps = @(Get-DemoProcesses $Port)
  foreach ($p in $ps) {
    if ($DryRun) { Dry "stop $($p.Name) $($p.ProcessId) on :$Port"; continue }
    & taskkill.exe /PID $p.ProcessId /T /F 2>&1 | Out-Null
  }
  return $ps.Count
}

function Get-PortHolder([int]$Port) {
  try {
    $o = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1 -ExpandProperty OwningProcess
    $p = Get-Process -Id $o -ErrorAction Stop
    return "$($p.ProcessName) (PID $o)"
  } catch { return 'an unknown program' }
}

# ---------------------------------------------------------------------------------- git bash
function Find-Bash {
  $cands = @(
    (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')
  )
  $git = Get-Command git.exe -ErrorAction SilentlyContinue
  if ($git) { $cands += (Join-Path (Split-Path (Split-Path $git.Source -Parent) -Parent) 'bin\bash.exe') }
  foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }   # never WSL's System32\bash.exe
  return $null
}

# Run a demo_up.sh command in its OWN hidden console, so the services it starts keep running when
# this window is closed. Its output is followed here line by line.
function Invoke-DemoUp([string]$Arguments, [int]$TimeoutSec = 900) {
  $log = Join-Path $LogDir "demo_up-$Stamp.log"
  $rel = "var/demo-day/logs/demo_up-$Stamp.log"
  if ($DryRun) { Dry "run (Git Bash) scripts/demo_up.sh $Arguments"; return 0 }
  Set-Content -Path $log -Value '' -Encoding ascii
  $p = Start-Process -FilePath $script:Bash -ArgumentList "-c `"bash scripts/demo_up.sh $Arguments >> $rel 2>&1`"" `
    -WorkingDirectory $Db -WindowStyle Hidden -PassThru
  $null = $p.Handle   # keeps ExitCode readable in Windows PowerShell 5.1
  $shown = 0
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ($true) {
    $done = $p.HasExited
    $lines = @(Get-Content -Path $log -ErrorAction SilentlyContinue)
    for ($i = $shown; $i -lt $lines.Count; $i++) { if ($lines[$i]) { Say "    | $($lines[$i])" 'DarkGray' } }
    $shown = $lines.Count
    if ($done) { break }
    if ((Get-Date) -gt $deadline) { Bad "demo_up.sh $Arguments did not finish in $TimeoutSec s; see $log"; return 1 }
    Start-Sleep -Milliseconds 700
  }
  return $p.ExitCode
}

# ------------------------------------------------------------------------------------ browser
function Find-Browser {
  $chrome = @((Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  $edge = @((Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if ($Browser -eq 'edge' -and $edge) { return $edge }
  if ($Browser -eq 'chrome' -and $chrome) { return $chrome }
  if ($chrome) { return $chrome }
  return $edge
}

function Close-DemoBrowser {
  $needle = $BrowserProfile.ToLower()
  $ps = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe' or Name='msedge.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($needle) })
  if ($ps.Count -eq 0) { return }
  if ($DryRun) { Dry "close the demo browser ($($ps.Count) processes)"; return }
  foreach ($p in $ps) { & taskkill.exe /PID $p.ProcessId /T /F 2>&1 | Out-Null }
  Start-Sleep -Milliseconds 800
}

function Open-DemoBrowser([string]$Url) {
  $exe = Find-Browser
  $flags = @(
    "--user-data-dir=`"$BrowserProfile`"",
    "--app=$Url",
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
    '--disable-notifications', '--disable-features=Translate,TranslateUI,MediaRouter,msEdgeTranslate',
    '--disable-component-update', '--hide-crash-restore-bubble', '--disable-session-crashed-bubble',
    '--autoplay-policy=no-user-gesture-required', '--force-device-scale-factor=1',
    '--window-position=0,0', '--window-size=1920,1080'
  )
  if (-not $Windowed) { $flags += '--start-fullscreen' }
  if ($NoBrowser) { Say "  (no browser: -NoBrowser)  $Url"; return }
  if (-not $exe) { Bad 'No Chrome or Edge found; open this in any browser:'; Say "    $Url" 'White'; return }
  if ($DryRun) {
    Dry "close any open demo browser, wipe $BrowserProfile, then run:"
    Say "    `"$exe`" $($flags -join ' ')" 'Magenta'
    return
  }
  Close-DemoBrowser
  # A clean profile every time: no extensions, no saved sessions (so every demo slot is signed out),
  # no zoom left over from rehearsal, no "restore pages" bubble.
  for ($i = 0; $i -lt 5 -and (Test-Path $BrowserProfile); $i++) {
    Remove-Item -Recurse -Force $BrowserProfile -ErrorAction SilentlyContinue
    if (Test-Path $BrowserProfile) { Start-Sleep -Milliseconds 600 }
  }
  New-Item -ItemType Directory -Force -Path $BrowserProfile | Out-Null
  Start-Process -FilePath $exe -ArgumentList ($flags -join ' ') | Out-Null
  Ok "Opened $([IO.Path]::GetFileNameWithoutExtension($exe)) (clean profile, app window, 100 %) at $Url"
  Say '  If the taskbar is still visible, click the window once and press F11.' 'Gray'
}

# ---------------------------------------------------------------------------------- checks
function Get-NewestSource([string]$Root) {
  $dirs = @('app', 'components', 'lib', 'public') | ForEach-Object { Join-Path $Root $_ }
  $files = @(Get-ChildItem -Path $dirs -Recurse -File -ErrorAction SilentlyContinue)
  $files += @(Get-ChildItem -Path $Root -File -Include 'next.config.mjs', 'package.json', 'tailwind.config.ts' -ErrorAction SilentlyContinue)
  $newest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($newest) { return $newest.LastWriteTime } else { return [datetime]::MinValue }
}

function Test-BuildFresh([string]$DistName) {
  $id = Join-Path $Web "$DistName\BUILD_ID"
  if (-not (Test-Path $id)) { return 'missing' }
  if ((Get-NewestSource $Web) -gt (Get-Item $id).LastWriteTime) { return 'stale' }
  return 'fresh'
}

function Test-Prereqs([bool]$NeedServices) {
  Head 'Prerequisites'
  $fatal = 0
  if ($NeedServices) {
    $script:Bash = Find-Bash
    if ($script:Bash) { Ok "Git Bash: $script:Bash" } else { Bad 'Git Bash not found. Install Git for Windows (https://git-scm.com/download/win)'; $fatal++ }
    if (Test-Path $Py) {
      & $Py -c 'import fastapi, uvicorn, httpx' 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) { Ok 'Python venv (databridge\.venv) with its packages' }
      else { Bad "The venv is missing packages. Fix: cd databridge; .venv\Scripts\python -m pip install -r requirements-lock.txt"; $fatal++ }
    } else {
      Bad 'No Python venv at databridge\.venv. Fix: cd databridge; py -3.13 -m venv .venv; .venv\Scripts\python -m pip install -r requirements-lock.txt'
      $fatal++
    }
    if (Test-Path (Join-Path $Db '.env.ib.local')) { Ok 'Settings file present (.env.ib.local): a model is tried at start (Hy3 if a TokenHub key is set; else the stand-in model if IB_CLAUDE=1)' }
    elseif ($env:TOKENHUB_API_KEY) { Ok 'TokenHub key in the environment: Hy3 is tried at start' }
    else { Warn 'No settings file and no TokenHub key (databridge\.env.ib.local or TOKENHUB_API_KEY): answers will use the extractive fallback (no model)' }
    # Where the sign-in passcode comes from (never its value).
    $pc = Get-DemoPasscode
    switch ($pc.Source) {
      'file' { Ok 'Sign-in passcode: read from .env.ib.local (the same value the Brain uses)' }
      'env' { Ok 'Sign-in passcode: from the IB_DEMO_PASSCODE environment variable' }
      'unreadable' { Warn 'Sign-in passcode in .env.ib.local uses shell syntax this kit does not read: set IB_DEMO_PASSCODE in the environment to the same value' }
      default { Ok 'Sign-in passcode: none set (any demo sign-in is accepted)' }
    }
  }
  $node = Get-Command node.exe -ErrorAction SilentlyContinue
  if ($node) { Ok "Node: $(& node.exe --version)" } else { Bad 'Node.js not found (https://nodejs.org)'; $fatal++ }
  if (-not (Test-Path (Join-Path $Web 'node_modules\next'))) {
    Warn 'Web app packages missing: running npm ci (needs internet, about 2 minutes)'
    if ($DryRun) { Dry 'npm ci in apps\internal_brain_web' }
    else {
      Push-Location $Web; & npm.cmd ci --no-audit --no-fund | Out-Host; Pop-Location
      if (Test-Path (Join-Path $Web 'node_modules\next')) { Ok 'Web app packages installed' } else { Bad 'npm ci failed'; $fatal++ }
    }
  } else { Ok 'Web app packages (node_modules)' }
  foreach ($c in @('clipA_compare_amy_rachel_ken.mp4', 'clipB_journey_audit_tamper.mp4', 'clipC_workbuddy_jordan.mp4')) {
    if (-not (Test-Path (Join-Path $Web "public\clips\$c"))) { Warn "Backup clip missing: public\clips\$c (key B will show nothing for that step)" }
  }
  # Laptop state the night-before checklist asks for (advice only).
  try {
    $bat = Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object -First 1
    if ($bat) {
      if ($bat.BatteryStatus -eq 2) { Ok "On mains power (battery $($bat.EstimatedChargeRemaining) %)" }
      else { Warn "On battery ($($bat.EstimatedChargeRemaining) %): plug the charger in" }
    }
  } catch { }
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    Say "  [INFO] Primary screen as Windows reports it: $($b.Width) x $($b.Height) (the preflight page checks the real pixels)" 'Gray'
  } catch { }
  return $fatal
}

function Get-ServiceState {
  [ordered]@{
    mock   = Test-Http 'http://127.0.0.1:8200/health'
    ib     = Test-Http 'http://127.0.0.1:8100/health'
    engine = Test-Http 'http://127.0.0.1:8300/health'
    web    = Test-Http "$LiveUrl/login" 10
  }
}

function Invoke-Preflight([string[]]$Extra) {
  Head 'Preflight and pre-warm'
  if ($DryRun) { Dry "run scripts/demo_preflight.py $($Extra -join ' ')"; return 0 }
  Push-Location $Db
  $env:PYTHONUNBUFFERED = '1'
  # demo_preflight.py reads the passcode from its environment: hand it the one the Brain uses (a
  # passcode kept only in .env.ib.local included) for this call only, then put the old value back.
  $hadPc = Test-Path Env:IB_DEMO_PASSCODE; $oldPc = $env:IB_DEMO_PASSCODE
  $pc = Get-DemoPasscode
  if ($pc.Value) { $env:IB_DEMO_PASSCODE = $pc.Value }
  try {
    & $Py scripts/demo_preflight.py @Extra | Out-Host
    $code = $LASTEXITCODE
  } finally {
    if ($hadPc) { $env:IB_DEMO_PASSCODE = $oldPc } else { Remove-Item Env:IB_DEMO_PASSCODE -ErrorAction SilentlyContinue }
    Pop-Location
  }
  return $code
}

function Show-Verdict([int]$Code, [string]$Base) {
  $model = ''
  try {
    $r = Get-Content $Report -Raw | ConvertFrom-Json
    if ($r.mode -eq 'offline') { $model = 'Model: OFFLINE MOCK (recorded fixtures). Say so on stage.' }
    elseif ($r.model.live) { $model = "Model: Tencent Hunyuan LIVE ($($r.model.chain)). Hy3 lines are allowed." }
    elseif ($r.model.ai) { $model = 'Model: AI model active (not Hy3). Say "an AI model, from the cited sources"; never claim Hy3.' }
    else { $model = 'Model: extractive fallback. Use the default lines: "quoted from the sources, with the same checks".' }
  } catch { }
  if ($DryRun) { Say ''; Say 'DRY RUN finished: nothing was changed.' 'Magenta'; return }
  if ($Code -eq 0) { Show-Big $ReadyArt 'Green' } else {
    Show-Big $NotReadyArt 'Red'
    Say '  Something above is FAIL (red). Run Reset; if it stays red, use the offline button (rung 3).' 'Red'
  }
  if ($model) { Say "  $model" 'White' }
  Say "  Presenter:  $Base$PresentPath" 'White'
  Say "  Preflight:  $Base/present/preflight   (big green/red board; sign in first)" 'White'
  Say "  Log:        $LogDir" 'DarkGray'
  Say ''
  Show-Keys
  Say ''
  Say '  You can close this window: the demo keeps running. To stop it, double-click "Stop Walnut Demo".' 'Gray'
}

# ------------------------------------------------------------------------------------ actions
function Start-Live([bool]$WarmAgain = $true, [string[]]$PreflightExtra = @()) {
  if ((Test-Prereqs $true) -gt 0) { Show-Big $NotReadyArt 'Red'; Say '  Fix the FAIL lines above, then double-click Start again.' 'Red'; return 1 }

  Head 'Ports'
  $s = Get-ServiceState
  foreach ($pp in @(@{ k = 'mock'; port = 8200 }, @{ k = 'ib'; port = 8100 }, @{ k = 'web'; port = 3100 })) {
    if ($s[$pp.k]) { Ok "Port $($pp.port): already running (reused)" }
    elseif (Test-Port $pp.port) {
      # Something holds the port but does not answer like our service.
      $n = Stop-DemoPort $pp.port
      if ($n -gt 0) { Warn "Port $($pp.port): a stuck demo process was stopped" }
      elseif (Test-Port $pp.port) { Bad "Port $($pp.port) is taken by $(Get-PortHolder $pp.port). Close that program, then Start again."; return 1 }
    } else { Ok "Port $($pp.port): free" }
  }
  # A web app that runs an older build than the kit (clips not served) is rebuilt. The preflight board
  # is not probed here: it answers 404 to anyone not signed in (round-4 Opus #2); demo_preflight.py
  # checks it as Maya once the services are up.
  if ($s['web']) {
    $stale = $Rebuild -or -not (Test-Http "$LiveUrl/clips/clipA_compare_amy_rachel_ken.mp4" 10)
    if ($stale) {
      Warn 'The running web app is an older build (clips not served): rebuilding it (about 2 minutes)'
      Stop-DemoPort 3100 | Out-Null
      Start-Sleep -Seconds 1
      $s['web'] = $false
    } elseif ((Test-BuildFresh '.next') -eq 'stale') {
      Warn 'The web build is older than some source files. Fine on a frozen build; Stop then Start rebuilds it.'
    }
  }

  Head 'Starting the stack (mock platforms :8200, Internal Brain :8100, engine :8300, web :3100)'
  if ($s['mock'] -and $s['ib'] -and $s['web'] -and $s['engine']) { Ok 'Everything is already running' }
  else {
    $code = Invoke-DemoUp 'start' 900
    if ($code -ne 0 -and -not $DryRun) {
      # Once seen in testing: a service did not come up right after a stop. One retry fixes that.
      Warn "demo_up.sh failed (exit $code): trying once more"
      Start-Sleep -Seconds 3
      $code = Invoke-DemoUp 'start' 900
    }
    if ($code -ne 0) { Bad "demo_up.sh failed (exit $code). The lines above say which part; logs in databridge\var\ib-final\logs"; Show-Big $NotReadyArt 'Red'; return 1 }
  }

  Head 'Health'
  $all = $true
  foreach ($h in @(@{ n = 'Mock platforms :8200'; u = 'http://127.0.0.1:8200/health'; r = $true }, @{ n = 'Internal Brain :8100'; u = 'http://127.0.0.1:8100/health'; r = $true },
      @{ n = 'Engine gateway :8300 (Developer > Brain only)'; u = 'http://127.0.0.1:8300/health'; r = $false }, @{ n = 'Web app :3100'; u = "$LiveUrl/login"; r = $true })) {
    if ($DryRun) { Dry "wait for $($h.u)"; continue }
    if (Wait-Http $h.u 60) { Ok $h.n } elseif ($h.r) { Bad "$($h.n) is not answering"; $all = $false } else { Warn "$($h.n) is not answering (optional)" }
  }
  if (-not $all) { Show-Big $NotReadyArt 'Red'; return 1 }

  # An Internal Brain that answers /health but cannot write its ledger (503 on sign-in) is restarted
  # once: it fails closed by design ("no log, no answer"), and a fresh process re-opens the ledger.
  if (-not $DryRun -and -not (Test-SignIn)) {
    Warn 'The Internal Brain answers but cannot sign anyone in (ledger unavailable): restarting it once'
    Stop-DemoPort 8100 | Out-Null
    Start-Sleep -Seconds 2
    $code = Invoke-DemoUp 'start' 600
    if ($code -ne 0 -or -not (Test-SignIn)) { Bad 'Sign-in still fails after a restart: Stop, then Start; if it persists use Reset -Fresh or the offline button'; Show-Big $NotReadyArt 'Red'; return 1 }
    Ok 'Internal Brain restarted; sign-in works'
  }

  $extra = @() + $PreflightExtra
  if ($WarmAgain -and -not $NoWarm) { $extra += '--warm' }
  $code = Invoke-Preflight $extra

  $mockState = Test-BuildFresh '.next-mock'
  if ($mockState -ne 'fresh') { Warn "Offline fallback build is ${mockState}: double-click 'Start Walnut Demo (offline)' once at T-60 to build it (about 2 minutes), then Stop" }

  Head 'Browser'
  Open-DemoBrowser "$LiveUrl$PresentPath"
  Show-Verdict $code $LiveUrl
  return $code
}

function Stop-All {
  Head 'Stopping Walnut DataBridge'
  Close-DemoBrowser
  $script:Bash = Find-Bash
  if ($script:Bash) { Invoke-DemoUp 'stop' 120 | Out-Null } else { Warn 'Git Bash not found: stopping by process only' }
  foreach ($port in @(3100, 8100, 8200, 8300, 3300, 3101)) {
    $up = Test-Port $port
    if ($up) { $n = Stop-DemoPort $port; if ($n -gt 0) { Say "  stopped :$port ($n processes)" } }
  }
  if (-not $DryRun) { Start-Sleep -Seconds 1 }
  foreach ($port in @(3100, 8100, 8200, 8300, 3101)) {
    if ($DryRun) { continue }
    if (Test-Port $port) { Warn ":$port is still taken by $(Get-PortHolder $port)" } else { Ok ":$port stopped" }
  }
  if ($DryRun) { Say ''; Say 'DRY RUN finished: nothing was changed.' 'Magenta' } else { Say ''; Say '  Stopped. Double-click "Start Walnut Demo" to start again.' 'Green' }
  return 0
}

function Reset-Demo {
  Head 'Reset to a known clean state'
  Say '  1. Simulator reset (admin.it): every platform permission back to the seeded world (about 1 minute)'
  Say '  2. All demo sessions signed out: the demo browser profile is wiped and reopened on the title card'
  Say '  3. The scripted questions are asked once more, so the first ask on stage is warm'
  Say '  The audit ledger is never edited (the tamper demo only ever touches a copy).'
  if ($Fresh) { Say '  -Fresh: the demo history is wiped and reseeded first (about 3 minutes)' 'Yellow' }
  if ($Fresh) {
    Close-DemoBrowser
    if ((Test-Prereqs $true) -gt 0) { return 1 }
    Head 'Fresh: stop the Brain and the platforms, wipe var\ib-final, reseed (the web app keeps running)'
    foreach ($port in @(8100, 8200)) { if (Test-Port $port) { Stop-DemoPort $port | Out-Null } }
    $code = Invoke-DemoUp 'start --fresh' 1200
    if ($code -ne 0) { Bad "demo_up.sh start --fresh failed (exit $code)"; Show-Big $NotReadyArt 'Red'; return 1 }
  }
  return (Start-Live $true @('--sim-reset'))
}

function Start-Offline {
  if ((Test-Prereqs $false) -gt 0) { Show-Big $NotReadyArt 'Red'; return 1 }
  $script:Bash = Find-Bash

  Head 'Offline mock build (.next-mock)'
  $state = Test-BuildFresh '.next-mock'
  $running = Test-Http "$MockUrl/login" 5
  if ($Rebuild -or $state -ne 'fresh') {
    Warn "Mock build is $state$(if ($Rebuild) { ' (-Rebuild)' }): building it now (about 2 minutes, no network needed)"
    if ($running) { Stop-DemoPort 3101 | Out-Null; $running = $false }
    if ($DryRun) { Dry 'npm run build:mock' }
    else {
      $blog = Join-Path $LogDir "build-mock-$Stamp.log"
      $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/c set NEXT_PUBLIC_IB_MOCK_MODEL=extractive&& npm run build:mock > `"$blog`" 2>&1" -WorkingDirectory $Web -WindowStyle Hidden -PassThru -Wait
      if ($p.ExitCode -ne 0 -or (Test-BuildFresh '.next-mock') -eq 'missing') { Bad "Mock build failed; see $blog"; Show-Big $NotReadyArt 'Red'; return 1 }
      Ok 'Mock build ready'
    }
  } else { Ok 'Mock build is up to date' }
  if ($running) { Ok "Offline app already running on $MockUrl (reused)" }
  else {
    if (Test-Port 3101) { Stop-DemoPort 3101 | Out-Null; Start-Sleep -Seconds 1 }
    if ($DryRun) { Dry "npm run start:mock (port 3101) in its own hidden console" }
    else {
      $slog = Join-Path $LogDir "start-mock-$Stamp.log"
      Start-Process -FilePath $env:ComSpec -ArgumentList "/c npm run start:mock > `"$slog`" 2>&1" -WorkingDirectory $Web -WindowStyle Hidden | Out-Null
      if (Wait-Http "$MockUrl/login" 90) { Ok "Offline app on $MockUrl" } else { Bad "Offline app did not start; see $slog"; Show-Big $NotReadyArt 'Red'; return 1 }
    }
  }
  $code = 0
  if (Test-Path $Py) {
    $extra = @('--offline')
    if (-not $NoWarm) { $extra += '--warm' }
    $code = Invoke-Preflight $extra
  } else { Warn 'No Python venv: skipping the scripted preflight; sign in, then check /present/preflight in the browser' }
  Head 'Browser'
  Open-DemoBrowser "$MockUrl$PresentPath"
  Show-Verdict $code $MockUrl
  Say '  OFFLINE MODE: answers are recorded fixtures. Say: "This is the recorded fixture mode, same screens."' 'Yellow'
  return $code
}

function Show-Status {
  Head 'Status'
  $rows = @(@{ n = 'Mock platforms'; u = 'http://127.0.0.1:8200/health' }, @{ n = 'Internal Brain'; u = 'http://127.0.0.1:8100/health' },
    @{ n = 'Engine gateway'; u = 'http://127.0.0.1:8300/health' }, @{ n = 'Web app (live)'; u = "$LiveUrl/login" }, @{ n = 'Web app (offline)'; u = "$MockUrl/login" })
  foreach ($r in $rows) { if (Test-Http $r.u) { Ok "$($r.n)  $($r.u)" } else { Say "  [ -- ] $($r.n)  $($r.u)" 'DarkGray' } }
  if (Test-Path $Report) {
    $r = Get-Content $Report -Raw | ConvertFrom-Json
    Say "  Last preflight: $($r.generated_at) ($($r.mode)), $(if ($r.ok) { 'GREEN' } else { 'RED' })" 'White'
  }
  return 0
}

function New-Shortcuts {
  $dest = $Target
  if (-not $dest) { $dest = [Environment]::GetFolderPath('Desktop') }
  Head "Shortcuts in $dest"
  $items = @(
    @{ n = 'Start Walnut Demo'; f = 'Start Walnut Demo.cmd'; i = 137 },
    @{ n = 'Stop Walnut Demo'; f = 'Stop Walnut Demo.cmd'; i = 131 },
    @{ n = 'Reset Walnut Demo'; f = 'Reset Walnut Demo.cmd'; i = 238 },
    @{ n = 'Start Walnut Demo (offline)'; f = 'Start Walnut Demo (offline).cmd'; i = 12 }
  )
  $sh = New-Object -ComObject WScript.Shell
  foreach ($it in $items) {
    $lnk = Join-Path $dest "$($it.n).lnk"
    if ($DryRun) { Dry "create $lnk"; continue }
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $Repo $it.f
    $s.WorkingDirectory = $Repo
    $s.IconLocation = "$env:SystemRoot\System32\shell32.dll,$($it.i)"
    $s.Description = "Walnut DataBridge Demo Day: $($it.n)"
    $s.Save()
    Ok $lnk
  }
  return 0
}

# ------------------------------------------------------------------------------------------ main
$Host.UI.RawUI.WindowTitle = "Walnut DataBridge - $Action"
$transcript = Join-Path $LogDir "$Action-$Stamp.log"
try { Start-Transcript -Path $transcript -Force | Out-Null } catch { }
Say ''
Say "Walnut DataBridge  |  Demo Day kit  |  $Action$(if ($DryRun) { ' (dry run)' })  |  $(Get-Date -Format 'ddd d MMM HH:mm:ss')" 'Cyan'
$script:Bash = $null
$rc = 0
switch ($Action) {
  'start' { $rc = Start-Live }
  'stop' { $rc = Stop-All }
  'reset' { $rc = Reset-Demo }
  'offline' { $rc = Start-Offline }
  'status' { $rc = Show-Status }
  'shortcuts' { $rc = New-Shortcuts }
}
try { Stop-Transcript | Out-Null } catch { }
exit ([int]($rc | Select-Object -Last 1))

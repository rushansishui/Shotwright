# Stop the running Shotwright server and its old console window, then start it again.
# Used by restart.bat. The old start.bat console is stopped too: a console whose server
# was killed keeps reading start.bat and could start a second, outdated server.
$root = Split-Path -Parent $PSScriptRoot
$parent = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId
$escaped = [regex]::Escape($root)
$targets = Get-CimInstance Win32_Process | Where-Object {
  $line = [string]$_.CommandLine
  $isConsole = $_.Name -eq 'cmd.exe' -and $line -match ($escaped + '\\(re)?start\.bat')
  $isServer = $line -match 'shotwright(\.exe)?"?\s+serve' -and ($line -match $escaped -or $line -match '^"?\.venv')
  ($isConsole -or $isServer) -and $_.ProcessId -ne $PID -and $_.ProcessId -ne $parent
}
foreach ($p in $targets) {
  Write-Host "[Shotwright] Stopping PID $($p.ProcessId)"
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if (-not $targets) { Write-Host '[Shotwright] No running server found.' }
Start-Sleep -Seconds 2
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('"' + (Join-Path $root 'start.bat') + '"') -WorkingDirectory $root

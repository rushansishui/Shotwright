# Exit code 0 when a Shotwright server from this folder is already running, 1 otherwise.
# Used by start.bat so a second start doesn't launch another server on the next free port.
$root = Split-Path -Parent $PSScriptRoot
$escaped = [regex]::Escape($root)
$found = Get-CimInstance Win32_Process | Where-Object {
  $line = [string]$_.CommandLine
  $line -match 'shotwright(\.exe)?"?\s+serve' -and ($line -match $escaped -or $line -match '^"?\.venv')
}
if ($found) { exit 0 } else { exit 1 }

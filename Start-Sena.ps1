param([switch]$Preview)
$ErrorActionPreference = 'Stop'
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe'
$app = Join-Path $PSScriptRoot 'widget.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'Python 3.12+ with Pillow is required for source runs. Use the Windows portable ZIP for a bundled runtime.' }
    $python = $command.Source
}
$launchArgs = @('-X', 'utf8', '-B', ('"' + $app + '"'))
if ($Preview) { $launchArgs += @('--preview','--language','en','--details') }
Start-Process -FilePath $python -ArgumentList $launchArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden

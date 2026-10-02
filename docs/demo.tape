# VHS tape for mswap demo recording
# Render with: vhs docs/demo.tape

Output docs/assets/demo.gif

Set Shell "powershell"
Set Theme "Catppuccin Mocha"
Set FontSize 16
Set Width 1100
Set Height 600
Set Padding 20

Hide
Type "$env:MSWAP_DEMO='1'; $env:MSWAP_NO_NETWORK='1'; $tempDir=[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(),'mswap-demo-tape'); New-Item -ItemType Directory -Force -Path $tempDir | Out-Null; $env:MSWAP_HOME=$tempDir; uv run python -m mswap __demo-seed; function mswap { uv run python -m mswap @args }; Clear-Host"
Enter
Sleep 1s
Show

Sleep 500ms
Type "mswap list"
Sleep 500ms
Enter
Sleep 3s

Type "mswap switch"
Sleep 500ms
Enter
Sleep 2s

Type "mswap list"
Sleep 500ms
Enter
Sleep 3s

Hide
Type "Remove-Item -Recurse -Force $tempDir"
Enter

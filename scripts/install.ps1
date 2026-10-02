$ErrorActionPreference = "Stop"

Write-Host "Installing mswap..."

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;" + $env:Path
}

Write-Host "Installing mswap from GitHub..."
$latest = ""
try {
    $release = Invoke-RestMethod https://api.github.com/repos/shaurya-disciplined/mswap/releases/latest -ErrorAction SilentlyContinue
    if ($release -and $release.tag_name) {
        $latest = $release.tag_name
    }
} catch {
    # Ignore errors
}

if (-not $latest) {
    $latest = "main"
}

uv tool install --force "git+https://github.com/shaurya-disciplined/mswap@$latest"

Write-Host "Installing mswap.cmd shim..."
$uvToolDir = uv tool dir
& "$uvToolDir\mswap\Scripts\python.exe" -m mswap shim install
$env:Path = "$env:USERPROFILE\.local\bin;" + $env:Path

Write-Host "mswap installed successfully!"
Write-Host "Next steps:"
Write-Host "1. Run 'agy' to sign in"
Write-Host "2. Run 'mswap add' to save the account"
Write-Host "3. Run 'mswap list' to view accounts"

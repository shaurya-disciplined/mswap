#!/bin/sh
set -e

echo "Installing mswap..."

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "Installing mswap from GitHub..."
latest=$(curl -sS https://api.github.com/repos/shaurya-disciplined/mswap/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
if [ -z "$latest" ] || [ "$latest" = "null" ]; then
    latest="main"
fi

uv tool install "git+https://github.com/shaurya-disciplined/mswap@$latest"

echo "mswap installed successfully!"
echo "Next steps:"
echo "1. Run 'agy' to sign in"
echo "2. Run 'mswap add' to save the account"
echo "3. Run 'mswap list' to view accounts"

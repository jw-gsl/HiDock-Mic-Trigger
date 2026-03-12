#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV"
else
    echo "Creating venv..."
    python3.13 -m venv "$VENV"
fi

echo "Installing requirements..."
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "Verifying torch + MPS..."
"$VENV/bin/python" -c "
import torch
print(f'torch {torch.__version__}')
if torch.backends.mps.is_available():
    print('MPS: available ✓')
else:
    print('MPS: not available (will use CPU)')
"

echo ""
echo "Done. Activate with:  source $VENV/bin/activate"

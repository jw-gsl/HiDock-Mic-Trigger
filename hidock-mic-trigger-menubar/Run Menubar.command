#!/bin/bash
set -euo pipefail
APP="$HOME/Applications/hidock-mic-trigger-menubar.app"
if [ ! -d "$APP" ]; then
  echo "App not found at: $APP"
  exit 1
fi
open "$APP"
echo "Requested launch of: $APP"

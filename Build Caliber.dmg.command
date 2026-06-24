#!/bin/bash
# Double-click to build Caliber.app + Caliber.dmg. This is the one-time publisher
# build; it shows progress, then leaves Caliber.dmg in this folder. End users never
# run this — they just open the .dmg you produce.
cd "$(dirname "$0")"
bash packaging/build_dmg.sh
echo ""
echo "Press Return to close…"
read -r _

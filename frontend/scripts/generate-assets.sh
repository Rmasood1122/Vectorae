#!/usr/bin/env bash
# ─── Vectorae — Mobile Asset Generation ───────────────────────
# Generates all required Android icon sizes and splash screen images from
# two source files using @capacitor/assets.
#
# TODO: verify @capacitor/assets is the current recommended approach for
#       the installed Capacitor version. Alternative: use cordova-res.
#       Check: https://capacitorjs.com/docs/guides/splash-screens-and-icons
#
# Prerequisites:
#   npm install -g @capacitor/assets    (run once)
#   Place source files as described below BEFORE running this script.
#
# ─── SOURCE FILE SPECIFICATIONS ──────────────────────────────────────────────
#
# 1. frontend/assets/icon-1024.png
#    • Dimensions: 1024 × 1024 px (square)
#    • Format: PNG, 32-bit RGBA
#    • Background: solid color (no transparency — Android adaptive icons
#      will add their own background layer)
#    • Safe zone: keep the logo within the center 768 × 768 px
#      (25% margin on each side) — Android may crop to a circle or rounded square
#    • No text — icons are too small for text to be legible
#
# 2. frontend/assets/splash-2732x2732.png
#    • Dimensions: 2732 × 2732 px (square, covers all device sizes)
#    • Format: PNG, 32-bit RGBA
#    • Background: should match your default theme's --color-background
#      (dark: #0f172a, light: #ffffff)
#    • Logo/content safe zone: center 512 × 512 px
#      (the rest is cropped differently per device resolution)
#    • Keep it simple — a centered logo on a solid background works best
#
# ─────────────────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")"
ASSETS_DIR="$FRONTEND_DIR/assets"

# Check source files exist
if [ ! -f "$ASSETS_DIR/icon-1024.png" ]; then
  echo "ERROR: Missing $ASSETS_DIR/icon-1024.png"
  echo "       Create a 1024×1024 PNG logo file at that path, then re-run."
  exit 1
fi

if [ ! -f "$ASSETS_DIR/splash-2732x2732.png" ]; then
  echo "ERROR: Missing $ASSETS_DIR/splash-2732x2732.png"
  echo "       Create a 2732×2732 PNG splash file at that path, then re-run."
  exit 1
fi

echo "Generating Android icons and splash screens..."

cd "$FRONTEND_DIR"

# TODO: verify exact @capacitor/assets CLI flags for the installed version
npx @capacitor/assets generate \
  --iconBackgroundColor '#0f172a' \
  --iconBackgroundColorDark '#0f172a' \
  --splashBackgroundColor '#0f172a' \
  --splashBackgroundColorDark '#0f172a' \
  --android

echo ""
echo "✓ Assets generated in android/app/src/main/res/"
echo ""
echo "Next steps:"
echo "  1. npm run mobile:build   (syncs assets to Android project)"
echo "  2. npm run mobile:open    (open Android Studio to verify icons)"

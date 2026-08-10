#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/autodl-tmp/minicpmo/Interaction-Model}"
DEMO_DIR="${DEMO_DIR:-/root/autodl-tmp/minicpmo/MiniCPM-o-Demo}"
PATCH_DIR="$PROJECT_DIR/patches"
BRAND_DIR="$PROJECT_DIR/assets/openinteraction"

apply_patch_file() {
  local patch_file="$1"
  if git -C "$DEMO_DIR" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
    echo "Already applied: $(basename "$patch_file")"
  elif git -C "$DEMO_DIR" apply --check "$patch_file"; then
    git -C "$DEMO_DIR" apply "$patch_file"
    echo "Applied: $(basename "$patch_file")"
  else
    echo "Patch does not match the installed MiniCPM-o-Demo revision: $patch_file" >&2
    exit 1
  fi
}

apply_patch_file "$PATCH_DIR/minicpmo-demo-camera-readiness.patch"

OMNI_HTML="$DEMO_DIR/static/omni/omni.html"
OMNI_APP="$DEMO_DIR/static/omni/omni-app.js"

if grep -q 'data-brand="openinteraction"' "$OMNI_HTML"; then
  echo "Already applied: minicpmo-demo-openinteraction-brand.patch"
else
  apply_patch_file "$PATCH_DIR/minicpmo-demo-openinteraction-brand.patch"
fi

if grep -q 'openinteraction_voice_zh.wav' "$OMNI_APP"; then
  echo "Already applied: minicpmo-demo-openinteraction-polish.patch"
else
  apply_patch_file "$PATCH_DIR/minicpmo-demo-openinteraction-polish.patch"
fi

if grep -q 'INTERACTION MODEL / PREVIEW' "$OMNI_HTML"; then
  echo "Already applied: minicpmo-demo-openinteraction-light-layout.patch"
else
  apply_patch_file "$PATCH_DIR/minicpmo-demo-openinteraction-light-layout.patch"
fi

if grep -q 'INTERACTION MODEL / PREVIEW' "$OMNI_HTML" && ! grep -q 'class="oi-intro"' "$OMNI_HTML"; then
  echo "Already applied: minicpmo-demo-openinteraction-compact-workspace.patch"
else
  apply_patch_file "$PATCH_DIR/minicpmo-demo-openinteraction-compact-workspace.patch"
fi

install -m 0644 "$BRAND_DIR/openinteraction-brand.css" \
  "$DEMO_DIR/static/openinteraction-brand.css"
install -m 0644 "$BRAND_DIR/faq/zh/omni.md" \
  "$DEMO_DIR/static/faq/zh/omni.md"
install -m 0644 "$BRAND_DIR/faq/en/omni.md" \
  "$DEMO_DIR/static/faq/en/omni.md"
echo "Installed OpenInteraction brand assets."

#!/usr/bin/env bash
#
# Reproduce the vendored design-skill environment from
# tools/design-tools.manifest.json (spec-doc6 RPN-SPEC-006 D4).
#
# The skill source is deliberately NOT committed: it is third-party code with
# its own licence and update cadence, so the repository pins a manifest and
# regenerates the tree instead of vendoring it.
#
# This script is deliberately loud about what it CANNOT guarantee. The manifest
# records a commit SHA for nothing, because the installers that produced this
# tree did not write one. Three of the four skills carry a content hash, which
# detects drift but cannot reconstruct a revision; impeccable carries neither,
# and it is 296 of the 302 vendored files and the one CI depends on.
#
# Re-running is safe: every installer below is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/tools/design-tools.manifest.json"

if [ ! -f "${MANIFEST}" ]; then
  echo "FATAL: ${MANIFEST} is missing. The manifest is the source of truth for" >&2
  echo "       this environment; without it there is nothing to reproduce." >&2
  exit 1
fi

command -v npx >/dev/null 2>&1 || {
  echo "FATAL: npx is not on PATH. Install Node.js first." >&2
  exit 1
}

echo "Reproducing the design-tool environment from tools/design-tools.manifest.json"
echo

# --- taste-skill: three skills, one repository ------------------------------
# Pinned by content hash in skills-lock.json. `npx skills add` reads that
# lockfile, so it restores the pinned bytes rather than the latest revision.
echo "==> Leonxlnx/taste-skill (design-taste-frontend, high-end-visual-design, redesign-existing-projects)"
npx --yes skills add Leonxlnx/taste-skill

# --- impeccable -------------------------------------------------------------
# NOT PINNED. Read the warning below before treating this step as reproducible.
echo
echo "==> impeccable"
echo "    WARNING: impeccable is not pinned by SHA or by content hash."
echo "    The installed tree declares version 4.1.1; the public npm registry"
echo "    currently publishes 3.6.0 under the name 'impeccable'. This step may"
echo "    therefore install a DIFFERENT tool than the one this repository was"
echo "    developed against, and frontend/scripts/impeccable-gate.mjs gates CI"
echo "    on it. Confirm the install channel with the product owner before"
echo "    relying on the result."
echo
npx --yes impeccable init

# --- verification -----------------------------------------------------------
echo
echo "==> Verifying"
missing=0
for skill in design-taste-frontend high-end-visual-design redesign-existing-projects impeccable; do
  if [ -d "${REPO_ROOT}/.claude/skills/${skill}" ]; then
    echo "    present: .claude/skills/${skill}"
  else
    echo "    MISSING: .claude/skills/${skill}" >&2
    missing=$((missing + 1))
  fi
done

if [ "${missing}" -ne 0 ]; then
  echo >&2
  echo "FAILED: ${missing} skill(s) did not install. Do not treat the design" >&2
  echo "        environment as reproduced." >&2
  exit 1
fi

echo
echo "Done. Note that the vendored source is gitignored by design; do not commit it."
echo "If you upgraded a tool, update tools/design-tools.manifest.json in the same"
echo "change, or the pin silently stops describing what is installed."

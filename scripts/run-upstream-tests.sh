#!/usr/bin/env bash
# Run Home Assistant core's own tests/components/insteon/ suite against this
# integration. Needs no PLM: the suite mocks pyinsteon.
#
#   scripts/run-upstream-tests.sh <ha-tag> [work-dir]
#
# Runs the suite twice, first on stock core (baseline) and then with
# custom_components/insteon copied over homeassistant/components/insteon, so
# failures can be attributed to this fork rather than to the environment.
set -euo pipefail

TAG="${1:?usage: $0 <ha-tag> [work-dir]}"
WORK="${2:-${TMPDIR:-/tmp}/ha-core-tests}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CORE="$WORK/core-$TAG"

mkdir -p "$WORK"
if [ ! -d "$CORE" ]; then
  git clone -q --depth 1 --branch "$TAG" https://github.com/home-assistant/core.git "$CORE"
fi
cd "$CORE"

# The clone's .python-version confuses pyenv-shimmed uv; pin a python that exists
# for the shim and let uv pick the interpreter core actually needs.
export PYENV_VERSION="${PYENV_VERSION:-3.13.9}"
PY="$(sed -E 's/.*>=([0-9]+\.[0-9]+).*/\1/' <<<"$(grep requires-python pyproject.toml)")"

if [ ! -x .venv/bin/python ]; then
  uv venv -q --python "$PY" .venv
  # requirements_test.txt is not enough for tests/conftest.py; add what the harness
  # and insteon's dependency components import.
  .venv/bin/python - <<'PY' > .insteon-test-reqs.txt
import json
reqs = ["bcrypt", "annotatedyaml", "async-timeout"]
for c in ["usb", "hassio", "http", "websocket_api", "panel_custom", "frontend", "dhcp", "mqtt"]:
    reqs += json.load(open(f"homeassistant/components/{c}/manifest.json")).get("requirements", [])
reqs += json.load(open("homeassistant/components/insteon/manifest.json"))["requirements"]
print("\n".join(reqs))
PY
  uv pip install -q --python .venv/bin/python -c homeassistant/package_constraints.txt \
    -e . -r requirements_test.txt -r .insteon-test-reqs.txt
  # Since core 2026.11 the test harness lives in an in-repo package.
  if [ -d packages/pytest-homeassistant ]; then
    uv pip install -q --python .venv/bin/python -e packages/pytest-homeassistant
  fi
fi

# The harness checks that every registered service has translations. Those are
# generated from strings.json, so build them or every setup test errors at
# teardown. Rebuilt on each run so this fork's strings.json changes are covered.
build_translations() { .venv/bin/python -m script.translations develop --all > /dev/null 2>&1; }

run() { .venv/bin/python -m pytest tests/components/insteon/ -q -p no:cacheprovider --no-header --timeout=120 "$@" 2>&1 | tail -30; }

echo "===== STOCK core $TAG ====="
git checkout -q -- homeassistant/components/insteon
build_translations
run || true

echo
echo "===== FORK over core $TAG ====="
rm -rf homeassistant/components/insteon
cp -R "$REPO/custom_components/insteon" homeassistant/components/insteon
cp "$REPO"/tests/test_*.py tests/components/insteon/
build_translations
run || true
git checkout -q -- homeassistant/components/insteon 2>/dev/null || true
rm -f tests/components/insteon/test_update_property_service.py

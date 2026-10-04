#!/bin/sh
# Reproduce everything: extraction -> merge -> citation backfill -> Spanish -> address resolution -> lookups/changes -> self-validation.
# LLM outputs are cached by content hash (cache/llm), so a rerun reproduces the same records. Delete cache/ to start from zero.
set -e
cd "$(dirname "$0")"
python3 pipeline/extract.py "$@"
python3 pipeline/merge.py | head -1
python3 pipeline/backfill.py
python3 pipeline/translate.py
python3 pipeline/resolve.py | tail -1
node pipeline/build.mjs
python3 pipeline/validate.py

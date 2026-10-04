#!/bin/sh
# Add a brand-new law text (new ordinance or new jurisdiction) and recompute every answer.
# usage: ./add_document.sh path/to/text.txt "Cambridge, MA"
set -e
cd "$(dirname "$0")"
python3 pipeline/extract.py --file "$1" --jurisdiction "$2"
python3 pipeline/merge.py | head -1
python3 pipeline/backfill.py
python3 pipeline/translate.py
node pipeline/build.mjs
python3 pipeline/validate.py || true

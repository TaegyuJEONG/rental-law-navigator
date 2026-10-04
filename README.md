# Rental Housing Law Navigator

Hack-Nation 7th Global AI Hackathon · Challenge 02 (RealPage) · team JTG.ai

For any of the 500 sample addresses and any "as of" date, the system answers: **which housing rules apply here, and how do the supplied change cases affect the answer?** Every answer cites the source text.

> **Not legal advice.** This is a prototype that shows what public law text says. It is not a compliance certification.

## What is in this repository

| Path | What it is |
|---|---|
| `submission/rules.json` | 56 rule records in the organiser schema, plus extra time, place and evidence fields |
| `submission/lookups.json` | Results for all 500 addresses as of 2026-10-01 |
| `submission/changes.json` | Affected addresses and conflict flags for T1–T5 |
| `submission/findings.json` | "No rule at this level" findings per jurisdiction and category |
| `submission/validation_report.json` | Output of the self-validation run |
| `pipeline/` | Extraction, merge, citation backfill, address resolution, build, validation |
| `web/` | Static web app. `web/engine.js` is the rules engine used by both the app and the build |
| `audit/log.jsonl` | One line per model call and pipeline step (model, prompt hash, document hash, token use) |
| `starter/` | The organiser starter pack, unchanged |
| `research/` | Link-only sources saved one page at a time. Used for research only, never counted as corpus citations |
| `METHOD.md` | One-page method note |

## Run it

Requirements: Python 3.10+, Node 18+, an OpenAI API key. No Python or Node packages are needed.

```bash
echo "OPENAI_API_KEY=sk-..." > .env
./run_all.sh
```

`run_all.sh` runs extraction → merge → citation backfill → Spanish → address resolution → lookups and change tests → self-validation. Model outputs are cached by content hash in `cache/llm/`, so a rerun reproduces the same records.

View the app locally:

```bash
python3 -m http.server 4173 --directory web
```

Add a new law text for a jurisdiction in scope and recompute every answer:

```bash
./add_document.sh path/to/ordinance.txt "Cambridge, MA"
```

A new jurisdiction needs three things: a line in `config/scope.json`, its law texts, and addresses in it. If it is in a new state, that state's statutes are needed too. This has not been done for any jurisdiction outside the starter pack.

## How it works

1. **Extract** (`pipeline/extract.py`). A language model reads each document and writes rule records. No rule is hand-coded. A validator drops any record whose `quoted_span` is not a verbatim substring of the source.
2. **Merge** (`pipeline/merge.py`). Records that describe the same law are clustered into one rule per law, per category, per jurisdiction. The quote is taken from distributed corpus text when one exists. Missing dates are filled from the other documents. Competing effective dates within a year raise a conflict flag.
3. **Backfill** (`pipeline/backfill.py`). For rules supported only by link-only pages, the model looks for a supporting passage in the distributed corpus. Code accepts it only if the quote is found there.
4. **Resolve** (`pipeline/resolve.py`). Census Geocoder batch → coordinates → incorporated place. The mailing city is never trusted.
5. **Apply** (`web/engine.js`). Deterministic code tests each rule's coverage against public building facts and computes status from dates for the query day. Missing facts give `unknown`. A state rule that yields to a stricter local rule is `superseded` where the local rule covers the address.
6. **Track** (`pipeline/build.mjs`). The five change tests are run with the same engine.

## Results (as of 2026-10-01)

- 56 rules: 50 in force, 1 not yet effective, 2 pending, 3 failed.
- 500 of 500 addresses resolved to the legal city counts in the participant guide.
- T1 250 affected · T2 90 · T3 140 affected, 90 conflict flags · T4 110 · T5 0.
- Self-validation: 13 of 13 checks pass (`python3 pipeline/validate.py`).

The organisers do not distribute `score.py` or an answer key, so these numbers are our own checks, not an official score.

## Known limits

- Owner names, many unit counts and many construction years are not in public assessor data. Those answers are `unknown`.
- Year built is not the certificate-of-occupancy date. Buildings in a cutoff year are `unknown`.
- Jersey City and Hoboken algorithmic-pricing bans, Newark ordinances and two Massachusetts regulations have no text in the distributed corpus. They are supported by link-only pages and marked as lower confidence.
- 23 addresses could not be matched by the Census batch geocoder. 15 were matched after normalising the street format. 8 were placed from resolved sample addresses with the same ZIP or postal city; they are marked as inferred and every answer for them says so.
- Exemptions for special property types (hotels, care facilities, condominiums and similar) are not checked, because assessor data cannot show them. Where a rule's date and unit tests pass, the result is `applies` and the explanation states what was not checked.
- Possible preemption of the Jersey City and Hoboken ordinances by the New Jersey FAIR Act is flagged for human review, not decided.
- Spanish text is machine translated.

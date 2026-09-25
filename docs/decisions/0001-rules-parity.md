# 0001 — Rules are an R port, pinned by a committed golden file

Date: 2026-09-25 · Status: accepted

## Context
The deterministic detectors live in structured_deidentification `app/R/detect_r.R`. slm_jev
needs the same candidates in Python, because the judge can only pick spans that were proposed.

## Decision
- `slmjev/rules.py` is a line-by-line port of `se_nric_valid`, `se_parse_compact_date`,
  `se_luhn`, `se_detectors`, `se_scan_text`, `se_dedup_overlaps` and `se_classify_value`.
- R is the source of truth. `tests/parity/dump_r.R` runs the real R code over the synthetic inputs
  in `tests/parity/cases.json`. It writes `tests/parity/r_expected.json`, which is committed, so
  the parity tests run without R.
- Spans keep the R contract: `start` is 1-based and `end` is inclusive. This matches what
  `detect_ner.py`, `detect_pf.py` and `detect_llm.py` already emit.

## Parity details worth knowing
- R's PCRE runs without UCP, so `\b`, `\s` and `\w` are ASCII-only. For example, `ÉS1234567D` and
  `café12345678` still match. Python therefore compiles with `re.ASCII | re.IGNORECASE`. Dropping
  `re.ASCII` fails the `unicode` parity case.
- In the compact-date validator, the time separator is case-sensitive, but the scanner regex is
  not. So `19901231t143000` is proposed at confidence 0.15 rather than 0.99.
- `scan_text` floors a failed validation at 0.1 and `classify_value` does not. No shipped detector
  reaches that floor today.

## Consequences
- Changing a rule means changing R first, re-running `dump_r.R`, and then following in Python.
- Not ported yet: watchlist and learned-pattern detectors (`se_watchlist_detector`,
  `se_learned_detectors`). They can be passed through `detectors(extra=[...])`.

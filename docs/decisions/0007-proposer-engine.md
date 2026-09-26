# 0007: Proposer, engine and the structured_deidentification backend (P6)

Date: 2026-09-26. Status: **accepted**, for synthetic data. The judge now runs end to end on its
own candidates, and `slm:jev` is an opt-in detector in structured_deidentification (SD).
Realistic text (P7) and latency are still open.

## Context
Up to P5 every eval used an **oracle proposer**: the gold spans plus planted decoys. In
production nobody hands the judge the gold spans, and the judge cannot pick a span nobody
proposed. So P6 needs three things:
1. a proposer that covers every identifier and SHI mention;
2. an engine with SD's transport (JSON on stdin, spans on stdout, one process per batch);
3. SD wiring that keeps the judge's fail-closed behaviour, because SD's other engines fail open.

## Decision

### Proposer (`slmjev/propose.py`)
High recall, surface shapes only. Precision is the judge's job, so the proposer over-proposes.
- **Sources:** the ported rules (`rules.scan_text`); numeric and written dates; phones; ID-like
  tokens (5+ digits); vehicle plates; name runs (Title-case or upper-case words, with Malay and
  Indian connectors `bin`, `binte`, `s/o`, `d/o`, `@`, and particles such as `de`, `van`), and the
  words after an honorific; addresses (street-type endings, `Jalan` / `Lorong`, and a
  unit-anchored pattern such as `Blk 5 Punggol Field, #05-12`); and a general SHI lexicon
  (terms, repeatable lead words such as "known" or "genetically confirmed", and head nouns such
  as "clinic", "medication", "counselling").
  - The lexicon holds general clinical terms only. It is not DAFA-derived.
- **Merging:** one candidate per distinct interval. Its `detector` joins every source that
  proposed it (`rule:nric+shape:idlike`), which the judge's fast path and the eval report use.
- **Extra candidates:** a request can add its own spans (e.g. Privacy Filter's). They are
  bound-checked and judged like the rest.

**Coverage:** a gold span counts as covered when one candidate holds all of its letters and
digits. Over 200 notes, 200 cells, 60 hard-decoy notes and 100 lab notes per split, coverage is
**100 % for every label** on train (seed 1000), dev (11) and test (31), at about 40 to 42
candidates per 1k characters.
- **The test split was looked at once**, after train and dev were at 100 %. It showed two
  partial covers: "Punggol Field" addresses written unit first, and "gender-affirming hormone
  therapy". The unit-anchored address pattern and the `hormone` heads fixed them; the test split
  was then not used for proposer work again. Treat test coverage as slightly optimistic.

### Engine (`slmjev/engine.py`, `schemas/span.v1.json`)
- **Flow per row:** propose → rule-certain spans first (fast path) → candidates inside an
  accepted fast-path span are skipped → the calibrated judge (`PROD`: Choice only, 4 rotations)
  decides the rest → overlapping spans are resolved.
- **Resolution:** a span inside a kept span of equal or higher rank (identifier > review) is
  dropped. **Partial overlaps are kept**, so no character of either span is lost.
- **Cells:** `kind: "cells"` shows the column header to the judge, and
  `column.date_outliers` flags misplaced dates. Free text gets no header.
- **Fail closed:**
  - A failed or unsure judgment is returned with `needs_review: true`, never dropped.
  - A review span whose top category is `none` still names the most likely identifier or SHI
    label (or the proposer's type hint), so SD has an action to apply.
  - A bad request, a missing file or a server failure exits 2 with `{"error": ...}` on stderr.
    It never prints an empty list.
- **Network:** `netguard.forbid_network()` runs first. The only socket is loopback to the
  llama-server the engine starts, on a **free port** with a per-run key. The server counts as up
  only when it accepts that key and refuses a keyless request, so a foreign server already on
  the port is never adopted.
- **No orphans:** on Windows the server runs in a job object that is closed with the engine
  process, so it dies even when the engine crashes. The crash below left two 2 GB servers
  running before this was added.
- **Loopback HTTP** goes through `http.client` (`netguard.loopback_request`), not
  `urllib.request`. SD's bundled Python aborted inside `urllib`: it builds a TLS context even for
  `http://`, and that OpenSSL build crashes when `SSLKEYLOGFILE` is set (the laptop's antivirus
  sets it). `urllib` would also honour proxy variables, which loopback calls must not.

### SD integration (branch `claude/slm-jev-backend` in structured_deidentification)
- **Runner:** `run_engine.py` mode `jev` imports `slmjev.engine` from `$SLMJEV_ROOT` (or the
  request's `slmjev_root`) and calls `scan`. Any exception returns `{"error": ...}`, not `[]`.
  SD's `_forbid_network()` is not applied, because it would also block the loopback judge;
  slm_jev's own guard applies instead.
- **R side** (`engine_py.R`):
  - `se_jev_config()` discovers the paths passively, by file existence only.
  - `se_jev_scan(strict=)` returns an empty frame with `attr(, "error")` on failure, or stops when
    strict.
  - `se_jev_frame()` maps identifiers to SD's free-text types (`national_id` → `nric`,
    `case_visit` → `case`, `device` → `serial`, `other_id` → `other`, every SHI label →
    `sensitive`). A missing confidence becomes 0, and `needs_review` stays true.
- **Fail-closed changes in SD:**
  - An unsure span passes the confidence floor: `confidence >= thr | needs_review`.
  - A failed scan raises a notification at detection and **stops the export**
    (`strict = TRUE`).
  - `se_dedup_findings` keeps a duplicate's `needs_review`.
  - `ft_types` gains `case`, `serial`, `biometric`, `photo`, `sensitive` and `other`, all
    selected by default. Before, `case` spans from SD's own rules were silently filtered out.
- **Surfaces:** a *Use slm:jev* checkbox in *Advanced* (off by default), `--jev` in
  `batch_cli.R`, `tools/smoke/smoke5_jev.R` (canned-output tests always run; a live scan runs
  when slm_jev is present) and a section in `docs/ner_packaging.md`.

## Results
P5 model and calibration (0006), `eval/e2e_eval.py`, synthetic only. Recall counts a gold span
as flagged when one identifier or review span covers all of its letters and digits. Precision
is the share of returned spans that overlap gold.

| Metric | Test (seed 31, 40 notes + 60 cells) | Hard (seed 41, 60) | Labs (seed 51, 100) |
|---|---|---|---|
| Gold spans | 252 | 120 | 154 |
| Recall, flagged (direct) | **1.000** (1.000) | **1.000** (1.000) | **1.000** (1.000) |
| Recall, auto-accepted | 0.968 | 1.000 | 1.000 |
| Label accuracy (flagged) | 0.984 | 1.000 | 1.000 |
| Precision, flagged | **1.000** | **1.000** | **1.000** |
| False accepts | **0** | **0** | **0** |
| Review rate | 0.032 (0.82 per 1k chars) | 0.000 | 0.000 |
| Latency, s per 1k chars (p50 / p95) | 59 / 261 | 107 / 147 | 105 / 139 |
| Candidates dropped by the judge | 54 | 61 | 164 |

These are the numbers **after the postal fix below**. Before it, labs missed one postal code
(recall 0.9935) and test and labs each had one `Singapore NNNNNN` span tagged `address`.

- **Test:** 0 misses, 0 partial covers, 0 false positives. Four label errors, all still
  flagged, so each is redacted under the wrong tag:
  - two malformed FIN-like IDs read as `case_visit` (review);
  - two case numbers read as `phone` (one auto-accepted).
- **Hard and labs:** no errors of any kind, and no spans sent to review. As in 0006 the scores
  are saturated on this generator's text, so an error on real text would also be confident.
- **Postal fix (found on the labs test set).** The first labs run missed `S276963` in
  "... #02-029 S276963.": it was fail-open. The proposer merges a postal code with its `S` /
  `S(` / `Singapore` lead into one candidate, and the rule fast path only accepted bare digits
  after that lead, so the model judged the merged span and dropped it with confidence. The same
  merge caused the `Singapore 456639` → `address` label error on test. `rule_certain` now also
  accepts the lead inside the span. Over every split's main, hard and labs sets (train, dev and
  test) the change touches 354 candidates, all gold postal codes, so it adds no false accepts.
  Because the gap was found on test, the post-fix test numbers are slightly optimistic.
- **SD smoke:** 16/16 on the live scan through SD's bundled Python 3.12, including the fast-path
  NRIC and a `known HIV` → `sensitive` span. SD's four existing suites still pass. Smoke 4
  (Privacy Filter) needs `SE_PYTHON` pointed at the bundled interpreter; without it, the PF scan
  cannot run on this laptop.

## Risks and open issues
- **Latency is the blocker for bulk use.** 59 s per 1k characters p50 (p95 261 s) on this CPU:
  about 40 candidates per 1k characters, each a 4-rotation Choice call. That is minutes per
  clinical note. SD also scans twice, at detection and again at export (as with Privacy Filter).
  Options, in order of expected gain:
  1. judge each candidate once at detection and have export reuse the reviewed findings;
  2. fewer rotations once calibration shows the bias is gone for the finetuned model;
  3. llama-server parallel slots (`-np`) with batched requests;
  4. cheaper candidates: a no-judge drop for proposer-only shapes that the model has never
     accepted on train.
- **Synthetic overfit** (as 0006): the proposer's patterns and the judge were both built on this
  generator's text, so these numbers overstate real performance. P7 must measure both on
  realistic text.
- **SD drops partial overlaps.** `se_redact_freetext_spans` keeps the wider of two overlapping
  spans and drops the other, so the narrower span's outside characters stay in the text. slm_jev
  keeps partial overlaps for this reason. This is pre-existing SD behaviour and affects every
  engine; it is flagged as a separate SD fix, not changed here.
- **Two copies of the same text.** SD passes cell text to a subprocess; it never leaves the
  machine, and the engine keeps nothing on disk. The llama-server log is off (DEVNULL) in the
  engine path.

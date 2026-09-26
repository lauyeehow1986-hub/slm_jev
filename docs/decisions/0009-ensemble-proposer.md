# 0009: Privacy Filter and Presidio persons as extra candidates (P11, P12)

Date: 2026-09-26. Status: **accepted** for synthetic data after the notes_v5 blind run (below). See
`eval-log.md`.

## Context
The judge can only accept a span that something proposed (CLAUDE.md, *select, don't generate*).
Three blind runs in a row failed the recall gate, and they failed for the same reason:

| blind set | recall | silent identifier misses | never proposed |
|---|---|---|---|
| notes_v2 (P8 code) | 0.931 | 18 | most |
| notes_v3 (P9 code) | 0.959 | 12 | most |
| notes_v4 (P10 code) | 0.934 | 24 | 21 |

Each round fixed the shapes that the previous set had shown. The next set, written by another
author, brought new ones. Shape-by-shape fixes to the proposer did not generalise far enough.

structured_deidentification already runs two learned detectors, Privacy Filter (`pf`) and
Presidio with spaCy (`ner`). Neither passes the gate on its own: on notes_v4, Privacy Filter
reaches recall 0.874 and Presidio's precision is 0.451. But they miss different things from the
proposer. Across the three blind sets, a Privacy Filter span overlapped 33 of jev's 52 identifier
misses and a Presidio `person` span another 3.

## Decision

### P11: engine spans are candidates, not answers
- `engine.scan_text(extra=...)` already merges extra candidates into the proposals. Each keeps
  its engine's name as a source.
- The judge decides on engine spans exactly as it decides on its own proposals. An engine span is
  never accepted just because an engine found it.
- `eval/bench.py` gained a `jev+<engine>[+<engine>]` system. It feeds those engines' spans, run in
  the same call or reused, to jev as extra candidates, and it adds their time to jev's.

### P12: feed only the engine types that pay
With every Presidio type fed in, notes_v4 recall rose to 0.989, but precision fell to 0.773
(105 false positives).
- Presidio `organization` and `date_time` spans were most of the new false positives. Examples:
  hospital and department names, `MRN`, `Day 4`, `72 hours`. The judge accepted some of them or
  sent them to review.
- Those two types added only three recalls (`BTC 22 118 406`, `SIVA`, `AP`).

So the configuration becomes `jev+pf+ner.person`: every Privacy Filter span, plus Presidio's
`person` spans only. The bench syntax `<engine>.<type>` selects types.

The proposer also gained broader shapes for what was left, each a class rather than one string:
- the value of an ID field label, spaces included (`MRN: BTC 22 118 406`,
  `donation no. W0417 26 118203 X`);
- a bare `@handle`;
- more relation and role words (`girlfriend`, `driver`, `named`, `known as`, `baby of`,
  `family of`, `witnessed`), and lists joined by `/`;
- a possessive before a relation (`Siva's wife`);
- a name or nickname in quotes;
- 2–3 capitals alone on a line, which sign a message.

## Blind result (notes_v5)
The P12 code was frozen (`results/notes_v5_blind_freeze.sha256`) and run once on notes_v5: 32
notes by a separate writer, 297 identifier spans, 5 notes with no PII.

| system | recall | covered | precision | F1 | silent identifier misses |
|---|---|---|---|---|---|
| **slm:jev + PF + Presidio persons** | **0.983** | 0.976 | **0.927** | **0.954** | 5 |
| slm:jev alone | 0.970 | 0.946 | 0.942 | 0.956 | 9 |
| rules + Privacy Filter | 0.899 | 0.822 | 0.969 | 0.932 | 30 |
| Privacy Filter | 0.845 | 0.781 | 0.951 | 0.895 | 46 |
| MediPhi-3.8B | 0.609 | 0.596 | 0.785 | 0.686 | 116 |

- Recall, precision and F1 pass the gate. Recall has one miss to spare: a sixth would make it
  0.980.
- The 5 misses are four reference numbers (`other_id`) and one given name. Their shapes are new
  again, so the proposer is still the weak point for rare ID formats.
- Calibration fails: ECE **0.143** against 0.05, at the candidate level. The judge's scores are
  saturated. SHI calls on negated or ordered-test mentions, and some headings called names, come
  out near 0.99. Addressed by 0010, which recalibrates the confidence without changing any
  decision.
- SHI stays weak (recall 0.583, precision 0.300). It is reported, not gated.

## Consequences
- **Latency.** The judge sees more candidates. On notes_v4, feeding every Presidio type doubled
  the time per 1k chars (43 to 93 s). The Privacy Filter and Presidio runs themselves take about
  1 s per 1k chars.
- **Dependence.** The release configuration depends on SD's bundled Privacy Filter and Presidio.
  Both already ship with SD and are air-gapped. slm:jev alone stays usable, with the lower recall
  above.
- **SD wiring.** SD users get this configuration only if SD passes the engine spans in. SD branch
  `claude/slm-jev-backend` does: `se_jev_candidates()` sends Privacy Filter spans and Presidio
  `person` spans to `se_jev_scan(candidates=)`, at detection and again at export.
- **Evidence.** The dev numbers on notes_v2 to notes_v4 are optimistic by construction. The gate
  is decided on the notes_v5 blind run.

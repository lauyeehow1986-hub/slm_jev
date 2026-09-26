# 0008: Closing the realistic-note gaps, validated on unseen sets (P8 to P10)

Date: 2026-09-26. Status: **accepted** for synthetic data; **superseded for the gate** by 0009.
See `docs/results.md` for the tables and `eval-log.md` for one row per run.

## Context
P7 (`docs/results.md`) found 7 silent identifier misses on sd20 and notes_v1. Five were proposer
gaps and two were judge drops. A fix found on a benchmark can only be trusted after it holds on
text nobody looked at while making it. So each round below:
1. fixes the misses found on the sets already seen (the **dev** sets);
2. freezes the code (`results/*_blind_freeze.sha256` holds the hashes of the set and of
   `slmjev/*.py`);
3. runs every system once on a **new, independently written** set that nobody had read (the
   **blind** run), and records that number as it came out;
4. only then reads the new set's errors, which turns it into a dev set for the next round.

The sets are invented and marked synthetic. notes_v2, notes_v3 and notes_v4 were each written by a
separate agent from a brief (note styles, label rules, checksummed NRICs, `example.*` mail
domains), not by the author of the generator or of the fixes. The notes_v4 brief asked for more of
the shapes that failed before: staff tags, masked and typo'd IDs, reference numbers with years,
non-Latin names, sign-offs and lists of relatives.

## Decision

### Harness
- **Comment bug.** `bench.parse_markup` dropped any line starting with `#` inside a note, so a
  line starting with a unit number (`#05-432`) was lost, and its gold offsets moved. Inside a note
  only `#` alone or `# ...` is now a comment. The notes_v2 blind report was re-scored with the
  offsets remapped (`bench_notes_v2_blind_remapped.json`), which changed precision from 0.900 to
  0.903 and nothing else.

### P8 (from P7's sd20 and notes_v1 misses)
- Proposer: `St` as a street type; a numbered street with no block (`Woodlands Ave 6`); one
  capitalised word after a role or relation word (`Nurse Lim`, `SON VIJAY`) or before a bracketed
  one (`Aisyah (daughter)`); name runs no longer cross a line break.
- Judge fast path: a record reference right after `Lab No` / `Accession` / `Specimen` /
  `Reg. No.` / `claim ref` / `policy no.` is `other_id` (`ref_keyword`), because the model had
  dropped lab numbers as `none`.

### P9 (from the notes_v2 misses)
- Proposer: relations before a bracketed name, lists of relatives, staff field labels (`PT:`,
  `DSA:`, `Bed 2 -`), inverted `SURNAME, Given` names, hyphenated given names, `Md.`, a trailing
  capitalised particle, `Dear X`; slash-joined IDs (`MOM/FDW/2026/33719`); scheme-less URLs with a
  path; messaging handles; phone extensions; file names with dates; dormitory addresses.
- Judge fast path: accession shapes (`HS26-018455`), handles after a messaging keyword
  (`WeChat ID`), scheme-less URLs with a path.

### P10 (from the notes_v3 misses)
- Proposer: initials after an honorific (`Dr R. Balakrishnan`); a staff tag in brackets
  (`Balan (MSW)`); `visitor`, `FDW`, `interpreter` as role words; sign-offs (`Thanks, Farhan`,
  `Regards,\nMeiling`); Han-character names in brackets or after `Name:`; a number with a year
  after a slash (`CC 1187/2026`); letter and digit segments joined by hyphens
  (`OPD-PT-26-33091`, `BIO-26-00918-A`), where the ID-like pattern had found only the digits.
- Judge fast path: a masked NRIC/FIN of 9 characters (`G****262U`) is `national_id`
  (`masked_nric`). The unmasked part still narrows down who it is.
- SHI lexicon: `HIV-1`/`HIV-2`, `reactive`, `G6PD deficiency`, `caesarean section`, drinking
  phrases, partner violence.

### Not changed, on purpose
- **SHI false positives** are mostly upper-case headings and drug names that the Choice question
  puts in an SHI category. Dropping name-shaped SHI calls in code would remove them, but it is a
  silent drop on a category whose gold is interpretive, so it was rejected. SHI stays a reported
  weakness; the SHI list is provisional and not DAFA-derived.
- **Contested gold.** Some sets label a mention of a biometric (`left thumbprint`) as a
  `biometric` identifier. It names a kind of record, not a person's data. It is reported
  separately and not fixed for.
- **The precision gate** is scored on the identifiers view (the 15 identifiers, with correct
  dates and SHI never counted as wrong), as P7 defined it. SHI precision is reported next to it.

## Blind results
| round | blind set | recall | precision | F1 | silent identifier misses |
|---|---|---|---|---|---|
| P8 | notes_v2 | 0.931 | 0.903 | 0.917 | 18 |
| P9 | notes_v3 | 0.959 | 0.959 | 0.959 | 12 |
| P10 | notes_v4 | 0.934 | 0.953 | 0.943 | 24 |

Each round passed on the sets it was fixed on and then failed on the next blind set. The shapes
that failed kept changing: single given names after new relation words, quoted nicknames,
initials, spaced IDs. That is the case for a proposer that does not depend on anyone listing the
shapes first. See 0009.

## Consequences
- Every fix is a surface shape or a keyword. None changes the model, the prompt or the
  calibration, so `calibration_p5.json` still applies (`PROMPT_VERSION` 2).
- The fast path grew. Each rule is narrow (a keyword lead or a full-string shape) and has a
  negative test, but each one bypasses the judge, so each one is a place a false positive can hide.
- Results on the dev sets (sd20, notes_v1, notes_v2, notes_v3) are optimistic by construction.
  Only the blind runs count for the gate.

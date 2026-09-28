# 0018: Pedigrees, spoken NRICs, log-in columns and feeder words (P21)

Date: 2026-09-29. Status: **accepted**. Blind on notes_v15: **FAIL** (see the last section).

## Context
P20 (0017) passed blind on notes_v13 and failed the repeat on notes_v14:
- direct-identifier recall 0.978 (264 of 270; 265 needed);
- precision 0.870;
- ECE 0.057.

The direct misses were:
- two year-only dates of death in a text pedigree;
- an NRIC spoken with its letters;
- the initials `KPL`;
- the lone given names `Hamidah` and `Suresh`.

Two lower-case log-in names in an audit log were also missed.

Most of the 45 false positives were:
- words of a Malay letter and fragments of a Tamil letter, which the feeders (the NER engine and Privacy Filter) called names;
- headings (`RADIOLOGY REQUEST`, `VISITOR LOG`, `BORANG PENDAFTARAN`);
- kin and pedigree words (`Proband`, `Grandson`);
- the letterhead lines that this set's writer left unmarked.

notes_v14 is the dev set for this step. notes_v12 and notes_v13 are regression checks.

## Decision
The judge's prompt wording, its thresholds and the calibration are unchanged.

**New fast paths (`rule_certain`, `slmjev/judge.py`).** Each is also proposed by `slmjev/propose.py`:
- **A spoken NRIC/FIN** (`S one two three four five six seven D`). A prefix letter, seven digit words and a check letter are read into digits. The result must pass the checksum.
- **A date of death after a death word.** A date or a bare year right after `d.`, `died`, `deceased`, `passed away`, `date of death`, `DOD` or `†` (`d. 1998`, `died on 3 Mar 2020`, `d. Apr 2012`). Only `in`/`on`, a colon or a dash may stand between the cue and the date.
- **A log-in in a fixed-width table.** A lower-case token of 4–21 characters (`lowjm`, `x_tanab`) in a column headed `USER`, `USER-ID`, `USERNAME`, `LOGIN` or `BY`. The header is found by walking up to a line of three or more all-caps cells two or more spaces apart. A blank line stops the search.

**New candidates:**
- **Script names in brackets after a Latin name** (`TAN AH KOW (สมชาย ใจดี)`). The whole script name is proposed.
- **A Latin name after a relation word of another script** (`மகன் Ravi`).
- **A sign-off with a department** (`Mariam, Medical Records.`): one word, a comma, then a department word.
- **Initials after `withdrawn`, `deferred` or `postponed`** (`5. Withdrawn: KLT`).
- **Syphilis tests** (`VDRL`, `RPR`, `TPHA`) are hiv_sti terms.
- **A disclosed assault by a relative or partner** (`husband hit her`, `employer hits her`) is proposed as other_sensitive.

**Fewer false candidates:**
- **A name run no longer spans a column gap.** Words of a name are joined by one or two spaces or a tab, not by a fixed-width table's wider gaps. A log's header (`TIMESTAMP USER-ID USER NAME`) is no longer one long "name".
- **Feeder spans made only of stop words are dropped.** A typed engine span with no digit whose words are all stop words is dropped (`Beliau`, `Saya`, `Proband`, `NRIC`). This applies only when no rule or shape proposed the same interval.
- **More stop words.** Malay words of address and letters (`beliau`, `saya`, `kepada`, `encik`, `puan`, `tuan`), pedigree and kin words (`proband`, `grandson`, `granddaughter`, `grandchild`, `nephew`, `niece`), `July`, and log headers (`timestamp`, `user`, `role`, `action`).
- **More heading words:** `request`, `log`, `register`, `borang`, `pendaftaran`, `mdt`.
- **Foreign function words and script fragments.** An engine name of two or more words that opens with a Tagalog/Malay/Indonesian function word is dropped. So is an engine name in another script with no Latin letter.

**Not done:**
- **Letterheads, switchboards and UENs.** Whether a letterhead address, a main switchboard number or a company registration number is marked is a writer's convention: notes_v13's writer marked them, notes_v14's did not. The system keeps proposing and flagging them, which is the fail-closed direction.
- **Lower-case single-word engine names** (`menjaga`). Gold lower-case names exist in earlier sets (`jun`, `fatimah`), so they are not filtered.
- **Known residuals:** `Yang`, `Johor Bahru`, `FNA Milan IV`, `Que Sera`, the code FPs (`DPO-2026-0412`, `FSN-2026-018`), and the SHI-other calibration (ECE 0.24 on 13 candidates in notes_v14).
- **`KPL`** is now proposed, but the judge answers `none`.

## Evidence (dev)
**The proposal diff against P20**, over sd20 and notes_v1–v14:
- 85 candidates removed and 34 added (25 on gold), before the assault pattern.
- **Gold newly covered:**
  - on notes_v14: `KPL`, `lowjm`, `Hamidah` and `Suresh`;
  - `VDRL` gold on notes_v2, v4, v6 and v12.
- **Lost:** month-only date_other spans on v9 and v10 (unscored), and `July` on notes_v13 (see below).
- The Thai name on notes_v13 is now proposed whole.

**The fast paths** fire 18 more times over notes_v1–v14 (`death_keyword`, `login_column`, `spoken_nric`), all on gold spans of the same label.

**The assault pattern** matches three places in all the sets:
- two on other_sensitive gold (notes_v7, notes_v13);
- one real disclosure beside the gold phrase in notes_v6, which scores as an SHI false positive.

It was added after the first notes_v13 dev run lost the SHI hit on `disclosed husband hit her in July`. That hit had come only from the `July` date candidate being judged other_sensitive. notes_v13 was re-run with the pattern; it matches nothing on notes_v12 or notes_v14, so their proposals are unchanged.

**The model re-run** (`jev+pf+ner.person`, `calibration_p13.json`). notes_v14 and notes_v13 are compared with their P20 blind runs, notes_v12 with its P20 dev run:

| set | recall | direct recall | precision | F1 | FPs | ECE | SHI recall |
|---|---|---|---|---|---|---|---|
| notes_v14 | 0.974 → 0.997 | 0.978 → **0.996** | 0.870 → **0.928** | 0.919 → 0.961 | 45 → 24 | 0.057 → **0.033** | 0.655 → 0.655 |
| notes_v13 | 0.983 → 0.983 | 0.985 → 0.985 | 0.917 → **0.923** | 0.949 → 0.952 | 26 → 24 | 0.047 → 0.046 | 0.529 → 0.529 |
| notes_v12 | 0.952 → 0.952 | 0.964 → 0.964 | 0.937 → **0.942** | 0.944 → 0.947 | 26 → 24 | 0.042 → 0.044 | 0.407 → 0.444 |

No set has a new miss.
- **Fixed on notes_v14:**
  - the pedigree deaths `1998` and `Mar 2011`;
  - the spoken NRIC;
  - `Hamidah`, `Suresh`, `lowjm` and `x_lowsm`.
- **Fixed on notes_v13:** the Thai name.
- **Left on notes_v14:** one silent miss (`KPL`). The rest are covered in part:
  - names split across lines;
  - OCR-corrupted digits;
  - inverted `SURNAME, Given` names.
- **The 24 notes_v14 FPs:**
  - the letterhead lines (about 12);
  - four Malay-letter words (`Yang`, `menjaga`, `Johor Bahru`, `N`);
  - `FNA Milan IV`, `Que Sera`;
  - codes.
- **Calibration by kind of call on notes_v14:**
  - fast path 0.029 (52);
  - identifier 0.023 (271);
  - none 0.038 (26);
  - SHI lexicon 0.046 (23);
  - SHI other 0.237 (13).
- **p95 per note** (sequential runs): notes_v14 109.0 s, notes_v13 107.1 s, notes_v12 189.3 s.

## Blind (notes_v15): FAIL
notes_v15 was written by a separate writer:
- 32 notes, 25,091 characters;
- 300 identifier gold (139 names, 255 direct), 37 SHI, 5 notes with no PII.

Its header marks initials used for a person as names, and leaves letterhead addresses, switchboard numbers, generic mailboxes and UENs unmarked.

Only its header was read before the run. The header check now stops at the first note. The P21 code and `models/calibration_p13.json` were frozen (`results/notes_v15_blind_freeze.sha256`); the hashes matched before and after the run.

| check | threshold | notes_v15 | |
|---|---|---|---|
| direct-identifier recall | ≥ 0.98 | 0.976 (249 of 255) | **fail**, by 1 span (250 needed) |
| precision | ≥ 0.90 | 0.894 | **fail** |
| ECE | ≤ 0.05 | 0.052 (402 candidates) | **fail** |
| F1 against MediPhi's 0.889 | > 0.889 | 0.933 on notes_v15; sd20 (dev) 1.000 | pass |
| p95 latency | recorded | 59.5 s per 1k chars (mean); per note p50 60.8 s, **p95 94.2 s** | recorded |

**FAIL**, on the same three checks as notes_v14, each by a thin margin.

- **Recall** 0.977, F1 0.933.
  - Every structured identifier reaches 1.000, as do `date_of_death`, `other_id` and `photo`.
  - `name` 0.964 on 139; `national_id` 0.962 on 26; `biometric` 0.5 on 2.
- **6 direct misses, none of them proposed:**
  - initials signing kardex lines (`RN AFR`, twice);
  - dotted initials (`H.K.L.`);
  - a surname alone on a sign-off line under a direct-fax line (`Ravindran`);
  - a given name in a contact-list row (`Thant`);
  - an NRIC split by spaces after a dotted leader (`NRIC ..... S 7709 506 C`).
  - A partial DNA profile (biometric) was also missed silently.
- **35 false positives** (8 on negative notes):
  - letterhead lines this writer left unmarked (9);
  - headings, units and fragments called names (13), including two people the writer did not mark (a second `Arjun`, `Dr Teo's`);
  - Singlish words (3);
  - equipment, form and protocol codes (8);
  - `Sat yong tau foo` called an address;
  - `son hits her`. P21's assault pattern proposed it for other_sensitive, and the judge called it a **name** (p 0.998).
- **Calibration by kind of call:**
  - rule fast path 0.032 (48);
  - identifier 0.051 (271);
  - none 0.020 (36);
  - SHI lexicon-proposed 0.158 (27);
  - SHI other 0.022 (20).

  The SHI-other bin that failed notes_v14 is well calibrated here; the lexicon bin is not.
- **SHI:** recall 0.622, precision 0.471.
- **Other systems:**

  | system | recall | precision |
  |---|---|---|
  | slm:jev alone | 0.930 | 0.924 |
  | rules + Privacy Filter | 0.820 | 0.910 |
  | Privacy Filter | 0.743 | 0.892 |
  | Qwen2.5-3B | 0.417 | 0.588 |
  | MediPhi-3.8B | 0.347 | 0.727 |
- **Post-hoc** (does not change the row): counting the two unmarked people as correct gives precision 0.8997, still under the gate.

notes_v15 is a dev set from here on. P21 is not a release candidate.

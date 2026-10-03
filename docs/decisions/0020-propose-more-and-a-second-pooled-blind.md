# 0020: Propose more, then a second pooled blind gate (P23)

Date: 2026-10-03. Status: **accepted**. Blind on notes_v19–v21 (pooled): **FAIL** on
direct-identifier recall (0.979, 826 of 844; 828 needed). Precision, ECE and F1 pass (see the
last section).

## Context
P22 (0019) failed the pooled blind gate on notes_v16–v18 on one check only:
- direct-identifier recall 0.970 (886 of 913; 895 needed);
- precision 0.945, ECE 0.014 and F1 0.956 passed with room to spare.

Of the 27 direct misses, 21 were never proposed. The judge cannot pick a span nobody proposed,
so the precision margin (0.945 against 0.90) is spent on proposing more. Each new shape is a
general form, not the missed string, and must add no new false positive on any dev set.

## Decision

### 1. New proposals (`slmjev/propose.py`)
- **Initials in more sign-off slots.**
  - after check and log captions (`Chk: WLH`, `Log maintained by NTH`);
  - a mixed-case line of its own (`HdS`);
  - a `/` list after a colon (`Staff on shift: ALW / RKN / JTM`), unless a part is a stop word
    or a staff role;
  - two- or three-letter initials and a hyphenated pair (`A-M`) in a sign-off.
- **Table and log columns.**
  - an `Init`/`Inits` header holds names;
  - a lower-case fixed-width header, as a log export writes it (`ts  user  act`), so log-in
    names in a `user` column are proposed;
  - a delimited cell under an ID header (`No`, `ID`, `Ref`, `Code`, `Serial`, or a header with
    `#`) is proposed when it holds a digit and a letter, a hyphen or five digits; a slash date
    is not;
  - a staff role alone in a name column (`MO`) is not a person.
- **Names in other scripts** after a Tamil, Hindi or Bengali honorific (`திரு.`, `श्री`).
- **A name after a Malay kin possessive** (`Suami saya Agus`).
- **IDs.**
  - a masked NRIC/FIN tail (`*842H`, `XXXX055Z`);
  - an ID that a line break cut after a hyphen, proposed whole (`4402-` / `118-73`);
  - an OCR'd NRIC or mobile number with letter-for-digit swaps (`l`, `I`, `O`, `o`). The NRIC
    must pass its checksum after the swap; the phone needs five real digits and a Singapore
    first digit (3, 6, 8 or 9).
  - a free-phone number (`1800 555 0192`).
- **A year of birth** after a sex marker (`M/1961`) or a YOB word (`YOB:`, `born in`), typed as
  a date.
- **A street ending in `Dr`** before a comma, the line end, a postal code or `Singapore`.

### 2. Judge changes (`slmjev/judge.py`)
- **Header cells may hold a full stop** (`BC No. | DOB`). Before, one abbreviated header hid the
  whole header row, so a DOB column went unseen and its dates were judged without it.
- **Table cells carry their column** into the family choice as well as the prompt.
- **New rule-certain spans** (no model call):
  - a masked NRIC/FIN tail → `national_id`;
  - a key=value pair whose key names an ID (`BB_TXN=`, `PAT_MRN=`) with a reference-shaped value
    of four or more digits → the key's identifier, else `other_id`;
  - a hyphen-wrapped ID after an ID keyword (`MRN 4402-` / `118-73`).
- **A year in a year-of-birth column** (`Sex/YOB`, `Birth year`) → `dob`, on the fast path.
- **An SHI category on a span from a person slot** is read as `name` when the span holds no
  sensitive term. A person slot is initials after a sign-off cue, a name column or a staff
  slot. The model had called a midwife's initials `other_sensitive`.
  - The first version applied this to every name-shaped span.
  - On notes_v18 that turned 17 headings and abbreviations into names (`AUDIT EXPORT`,
    `QDS PRN`, `first`), because the model gives those SHI calls too.
  - Over all dev sets, every SHI call on a person-slot span was a gold name (8 of 8).

Calibration is unchanged (`models/calibration_p22.json`), and so are the thresholds and the gate.

### 3. A second pooled blind gate
Three new sets, notes_v19–v21, 32 notes each, by three new writers. Each writer read only its
brief: the same format, labels and fixed annotation convention as notes_v16–v18, and a domain
that no earlier writer had:
- primary, community and long-term care;
- research, registry, quality and data-management records;
- patient- and family-written text and cross-border records.

Same protocol as 0019: only the headers are read before the run, the code, calibration and sets
are hashed first, the sets run one after another, and `eval/pool.py` decides on pooled counts.

**A disclosure.** The writers' hand-back reports gave label counts and listed their deliberately
invalid or masked IDs, as their briefs asked. The person-slot narrowing above was made after
those reports arrived. It came from notes_v18 and the dev sets only, and it touches nothing those
reports describe.

## Evidence (dev)
**The model re-run** with the P23 code on seven dev sets, against each set's previous run:

| set (baseline) | recall | direct recall | precision | FPs | misses fixed |
|---|---|---|---|---|---|
| notes_v18 (P22 blind) | 0.943 → 0.997 | 0.939 → 0.996 (263 of 264) | 0.923 → 0.927 | 24 → 24 | 16 |
| notes_v17 (P22 blind) | 0.974 → 0.995 | 0.984 → 0.997 (365 of 366) | 0.955 → 0.956 | 20 → 20 | 9 |
| notes_v16 (P22 blind) | 0.984 → 1.000 | 0.982 → 1.000 (283 of 283) | 0.953 → 0.953 | 15 → 15 | 5 |
| notes_v14 | 0.997 → 1.000 | | | 24 → 24 | 3 |
| notes_v12 | 0.954 → 0.971 | | | 24 → 24 | 8 |
| notes_v11 | | | | 23 → 22 | |
| notes_v9 | unchanged | | | | |

- **No new miss and no new false positive on any set.**
- **Pooled over notes_v16–v18:** direct 0.998 (911 of 913), precision 0.947 (1,043 of 1,102),
  ECE 0.018, F1 0.971 (`results/pooled_v16_v18_dev_p23.json`). These sets are where the misses
  came from, so this only shows the fixes work where they were made.
- **The first SHI-on-name override** added 17 false positives on notes_v18 (headings and
  abbreviations called names). Narrowing it to person-slot sources (section 2) removed all 17;
  notes_v17 and v18 were re-run with the narrowed code (`dev_p23b_*`).
- These are dev numbers; they do not gate anything.

## Evidence (blind): notes_v19–v21, pooled
**Run.** The freeze (`results/notes_v19_v21_blind_freeze.sha256`) matched before the run and
after each set. The three sets ran one after another (17:39–20:44 on 2026-10-03), with all
systems, so the latency is valid. Only the headers were read before the run. `eval/pool.py`
wrote `results/pooled_v19_v21_blind.json`.

| set | recall | direct recall (95% CI) | precision (95% CI) | F1 | ECE (95% CI) |
|---|---|---|---|---|---|
| notes_v19 | 1.000 | 1.000 (273 of 273; 0.986–1.000) | 0.908 (304 of 335; 0.872–0.934) | 0.952 | 0.020 (384; 0.013–0.055) |
| notes_v20 | 0.946 | 0.973 (291 of 299; 0.948–0.986) | 0.911 (346 of 380; 0.878–0.935) | 0.928 | 0.045 (477; 0.028–0.087) |
| notes_v21 | 0.963 | 0.963 (262 of 272; 0.934–0.980) | 0.882 (290 of 329; 0.842–0.912) | 0.921 | 0.047 (380; 0.029–0.078) |
| **pooled** | 0.968 | **0.979** (826 of 844; 0.967–0.987) | **0.900** (940 of 1,044; 0.881–0.917) | **0.933** | **0.031** (1,241; 0.018–0.054) |

**Verdict: FAIL.**
- Direct-identifier recall is 0.979. The gate needs 0.98, which is 828 of 844, so it fails by
  2 spans.
- Precision 0.900 passes, at the threshold. ECE 0.031 and F1 0.933 (MediPhi: 0.889) pass.
- Latency: 61.4 s per 1k characters (mean). The per-note p95 is 108.7 s, 139.9 s and 95.5 s.

**notes_v19 passes on its own; notes_v20 and notes_v21 fail.** 17 of the 18 direct misses were
never proposed:
- **notes_v20** (research, registry, quality and data-management records), 8:
  - initials with hyphens in an `Initials:` field (`N-A-R`), and after a dated receipt line
    (`- KX`);
  - a lower-case given name in the last column of a CSV export (3);
  - a name in an XML element whose tag is not a person field (`<Abstractor>`);
  - two bare NRIC tails after a "last 4" phrase, with no mask character (`815J`).
- **notes_v21** (patient- and family-written text and cross-border records), 10:
  - sign-off initials: one upper-case pair that Privacy Filter proposed and the judge called
    SHI (sent to review at 0.04), and two lower-case (`- jpm`);
  - a name after an Indonesian honorific (`Tn.`), a sentence-initial given name, and a name in
    a log row;
  - a masked phone (`8xxx 6631`) and an Indonesian phone with an area code in brackets;
  - a Philippine barangay address and its postcode.

**Other misses** (not direct): 13 study and biobank sample codes as other_id in notes_v20 (site
codes such as `TTSH-017`, aliquot codes), plus a device serial and a biometric template.

**False positives.** There were 104 over the three sets, 13 of them on negative notes:
- organisation names called names (a community club, an optician, a shipping firm, an
  insurer);
- headings and captions in capitals;
- Malay, Indonesian and Tamil words, and kinship terms;
- overseas cities and provinces called addresses;
- protocol, approval and incident codes called case or visit numbers;
- column names in a data codebook (`pid_hash`, `postal_sector`);
- fragments of longer spans.

**Calibration.** The pooled ECE is 0.031 on 1,241 candidates. By kind of call: fast path 0.010;
identifier 0.024, 0.032 and 0.072; "none" 0.051, 0.198 and 0.007; SHI lexicon 0.16–0.21; SHI
other 0.03–0.08.

**What this means.**
- Proposing more worked where it was aimed: notes_v19, written in the clinical and community
  styles the earlier sets covered, reached direct recall 1.000.
- Two new domains (data-management records; patient-written and cross-border text) brought new
  places again, and the precision margin is now spent: pooled precision sits at the gate.
- The pattern of P8–P23 holds. Each round fixes what the last writers did, and the next writers
  find new shapes. The miss count is falling (27 → 18 direct, on similar volumes), but not
  to the 2% the gate allows.

notes_v19–v21 are dev sets from here on.

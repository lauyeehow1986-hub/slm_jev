# Synthetic Singapore corpus (P2)

Invented clinical notes and table cells with gold PII/SHI spans. **Everything is fake**:
- names are drawn from common-name pools;
- every number is random;
- emails use the reserved `example.*` domains.

The generator code is committed. The generated data is not: `out/` is gitignored.

```
uv run python data/synthetic/generate.py            # seed 7, 2000 notes + 1500 cells in train
uv run python data/synthetic/generate.py --seed 8 --notes 500 --cells 400
```

This writes `out/{train,dev,test}.jsonl` and `out/manifest.json`, which records the generator
version, the labels version, the seed and each file's SHA-256. Record the manifest hash in
`docs/decisions/eval-log.md` for every eval run.

## Record format (one JSON object per line)

| field | meaning |
|---|---|
| `id` | `<split>-note-00042` or `<split>-cell-00042` |
| `kind` | `note` (free text) or `cell` (one table value) |
| `template` | note template (`admission`, `discharge`, `referral`, `nursing`, `social_work`, `death_summary`, `admin`, `negative`) or cell kind (`proper`, `clean`, `misplaced`, `buried`) |
| `column` | cells only: the column the value sits in |
| `text` | the text to scan |
| `spans` | gold PII/SHI: `start` (1-based), `end` (inclusive), `match`, `label`, `type`, `attrs` |
| `decoys` | things a rule may propose that are *not* PII (dates of admission, BP, doses, ICD codes, ward/bed) |
| `meta` | cells: `misplaced`, `value_type`, `buried` |

- `label` is one of the 15 identifiers or 6 SHI labels in `schemas/labels.v1.json`.
- `type` is the finer kind (`nric`, `fin`, `temp_ic`, `passport`, `vehicle`, `url`, …). It maps to
  `label` via `types` in that file.

## Annotation conventions

- **Names:** the span excludes titles (`Mdm `, `Dr `). Short forms (`Mr Tan` → `Tan`) and initials
  still count as names. `attrs` carries `ethnicity` (chinese / malay / indian / eurasian), `role`
  (patient / nok / clinician) and `form`.
- **Address and postal code are separate spans.**
  - `address` covers the block or house number, street, unit and building, but never
    "Singapore" or the postal code.
  - `postal_code` is the 6 digits alone.
  - Both carry `attrs.property`: `hdb`, `condo`, `landed`, or `unknown` for a bare postal cell.
- **Dates:**
  - Gold dates are the date of birth (`attrs.role = "dob"`) and the date of death
    (`attrs.role = "death"`).
  - Every other date (admission, discharge, procedure, lab, clinic, other) is a **decoy** that
    carries its role.
  - `attrs.format` records the surface form.
  - DOB agrees with the stated age, relative to 2025.
- **SHI** spans cover the condition, test or treatment phrase. Negated mentions ("No history of
  syphilis") are still spans, with `attrs.negated = true`, so the policy decides what to do
  with them.
- **Formats with no validator:**
  - Vehicle plates, MCR-style licence numbers and insurer member numbers look plausible but
    are not verified against any authority.
  - NRIC/FIN check letters *are* valid (`rules.nric_valid`).
  - Card numbers pass Luhn.

## Split hygiene

- Name components, streets and condo names are partitioned: `test` uses every 4th pool entry and
  `train`/`dev` use the rest.
- As a result, a test name or address never appears in train, which `tests/test_synth.py`
  checks.
- The random stream is seeded from `(generator version, seed, split)`, so each split is
  reproducible on its own.

## Known limits

- Templates are short and regular. Real notes have typos, abbreviations and run-on text, and
  are still needed for honest numbers.
- There are no `biometric` or `photo` spans: these rarely appear as text.
- SHI labels are provisional (see `schemas/labels.v1.json`, `shi_provisional`).

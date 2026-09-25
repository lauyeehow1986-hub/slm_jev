# dump_r.R — run structured_deidentification's detect_r.R over tests/parity/cases.json and
# write the golden tests/parity/r_expected.json that tests/test_rules.py compares against.
#
# Usage (from the repo root):
#   & "C:\Program Files\R\R-4.5.2\bin\Rscript.exe" tests\parity\dump_r.R
# Set SLMJEV_SD_ROOT to the structured_deidentification checkout (the folder holding
# app/R/detect_r.R) if it is not found automatically.

args <- commandArgs(trailingOnly = FALSE)
script <- normalizePath(sub("^--file=", "", args[grep("^--file=", args)][1]), winslash = "/")
here <- dirname(script)

find_sd_root <- function() {
  env <- Sys.getenv("SLMJEV_SD_ROOT", "")
  if (nzchar(env)) return(env)
  rel <- file.path("structured_deidentification", ".claude", "worktrees",
                   "shiny-deidentification-app-6a7932")
  dir <- here
  repeat {
    cand <- file.path(dirname(dir), rel)
    if (file.exists(file.path(cand, "app", "R", "detect_r.R"))) return(cand)
    if (dirname(dir) == dir) break
    dir <- dirname(dir)
  }
  stop("detect_r.R not found; set SLMJEV_SD_ROOT")
}

sd_root <- find_sd_root()
`%||%` <- function(a, b) if (is.null(a)) b else a
source(file.path(sd_root, "app", "R", "detect_r.R"))

cases <- jsonlite::fromJSON(file.path(here, "cases.json"), simplifyVector = FALSE)

span_records <- function(sp) {
  if (!nrow(sp)) return(list())
  lapply(seq_len(nrow(sp)), function(i) as.list(sp[i, , drop = FALSE]))
}

scan <- lapply(cases$scan, function(cs) {
  dets <- se_detectors(postal6 = isTRUE(cs$postal6))
  sp <- se_scan_text(cs$text, dets)
  list(id = cs$id, spans = span_records(sp), dedup = span_records(se_dedup_overlaps(sp)))
})

classify <- lapply(cases$classify, function(cs) {
  hits <- se_classify_value(cs$value, se_detectors(postal6 = isTRUE(cs$postal6)))
  list(value = cs$value, postal6 = isTRUE(cs$postal6),
       hits = if (length(hits)) as.list(hits) else setNames(list(), character(0)))
})

nric <- lapply(cases$nric, function(x) list(x = x, ok = se_nric_valid(x)))

compact_date <- lapply(cases$compact_date, function(s) {
  d <- se_parse_compact_date(s)
  list(s = s, date = if (is.na(d)) NULL else format(d, "%Y-%m-%d"))
})

luhn <- lapply(cases$luhn, function(x) list(x = x, ok = se_luhn(x)))

out <- list(
  generated_by = "tests/parity/dump_r.R",
  r_version = R.version.string,
  pcre_version = unname(extSoftVersion()[["PCRE"]]),
  scan = scan, classify = classify, nric = nric, compact_date = compact_date, luhn = luhn
)

dest <- file.path(here, "r_expected.json")
writeLines(jsonlite::toJSON(out, auto_unbox = TRUE, digits = NA, null = "null", pretty = TRUE),
           dest, useBytes = TRUE)
cat("wrote", dest, "\n")

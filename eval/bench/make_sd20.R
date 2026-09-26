# make_sd20.R — rebuild structured_deidentification's 20-note LLM A/B gold set (the one behind
# "MediPhi F1 0.889" in its docs/ner_packaging.md) and write eval/bench/sd20.json.
#
# The notes and gold spans are copied from SD's A/B harness (ab_llm.R, 2026-09-06). Its three
# NRIC/FIN values were drawn with set.seed(2026) and SD's se_nric_valid(), so this script
# sources detect_r.R and draws them the same way. Synthetic data only.
#
# Usage (from the repo root):
#   & "C:\Program Files\R\R-4.5.2\bin\Rscript.exe" eval\bench\make_sd20.R
# Set SLMJEV_SD_ROOT to the structured_deidentification checkout if it is not found.

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

`%||%` <- function(a, b) if (is.null(a)) b else a
source(file.path(find_sd_root(), "app", "R", "detect_r.R"))

valid_nric <- function(prefix = "S") {
  repeat {
    digits <- paste0(sample(0:9, 7, replace = TRUE), collapse = "")
    for (L in LETTERS) { cand <- paste0(prefix, digits, L); if (se_nric_valid(cand)) return(cand) }
  }
}
set.seed(2026)
NR1 <- valid_nric("S"); NR2 <- valid_nric("T"); NR3 <- valid_nric("S")

G <- list()
g <- function(text, ...) G[[length(G) + 1L]] <<- list(text = text, spans = list(...))
# SD's types, mapped to slm_jev labels; "date" (admission/appointment) is outside the 15
sp <- function(match, type) list(match = match, label = switch(type,
  nric = "national_id", postal = "postal_code", date = "date_other", type))

g("Patient John Tan seen today, follow-up with Dr Sarah Lim next week.",
  sp("John Tan","name"), sp("Sarah Lim","name"))
g("Spoke to daughter Mary Goh on 9123 4567 re discharge plan.",
  sp("Mary Goh","name"), sp("9123 4567","phone"))
g(paste0("Referred by Dr Rajesh Kumar; wristband NRIC ", NR1, " confirmed."),
  sp("Rajesh Kumar","name"), sp(NR1,"nric"))
g("Contact next of kin at tan.family@gmail.com or call +65 8123 4567.",
  sp("tan.family@gmail.com","email"), sp("+65 8123 4567","phone"))
g("Lives at Blk 123 Tampines St 11 #05-432, Singapore 521123.",
  sp("Blk 123 Tampines St 11 #05-432","address"), sp("521123","postal"))
g("DOB 12/03/1975, admitted 04/07/2023 under Dr Lee Wei Xiong.",
  sp("12/03/1975","dob"), sp("04/07/2023","date"), sp("Lee Wei Xiong","name"))
g("Nurse Priya Devi documented vitals; patient stable overnight.",
  sp("Priya Devi","name"))
g("Call husband Ahmad bin Ismail, hp 8123 9977 after 6pm.",
  sp("Ahmad bin Ismail","name"), sp("8123 9977","phone"))
g(paste0("Old records under name Grace Wong-Lim, ic ", NR2, "."),
  sp("Grace Wong-Lim","name"), sp(NR2,"nric"))
g("Seen with wife Madam Ng Bee Choo; Malay translator arranged.",
  sp("Ng Bee Choo","name"))
g("Fax the report to 6789 0123 attention Dr Chua in cardiology.",
  sp("6789 0123","fax"), sp("Chua","name"))
g("Email the echo results to sister at siti.rahman@example.com please.",
  sp("siti.rahman@example.com","email"))
g(paste0("Patient Kumar s/o Ramasamy, staying at Woodlands Ave 6, ic ", NR3, "."),
  sp("Kumar s/o Ramasamy","name"), sp("Woodlands Ave 6","address"), sp(NR3,"nric"))
g("Confirmed next appointment 15 Jan 2024 with the cardiology clinic.",
  sp("15 Jan 2024","date"))
g("Spoke with Dr Tan and Nurse Lim regarding the transfer.",
  sp("Tan","name"), sp("Lim","name"))
g("Mother Faridah bte Osman reachable at +65 9012 3456 daytime.",
  sp("Faridah bte Osman","name"), sp("+65 9012 3456","phone"))
g("Patient reviewed in clinic; stable, no acute concerns.")
g("Routine follow up, nil significant, plan repeat echo in 6 months.")
g("Bloods sent, awaiting troponin and BNP results this afternoon.")
g("Chest pain resolved with GTN, ECG unremarkable, discharged well.")

docs <- lapply(seq_along(G), function(i) {
  t <- G[[i]]$text
  spans <- lapply(G[[i]]$spans, function(z) {
    pos <- regexpr(z$match, t, fixed = TRUE)
    if (pos < 0) stop("gold not found in note ", i, ": ", z$match)
    list(start = as.integer(pos), end = as.integer(pos + nchar(z$match) - 1L),
         match = z$match, label = z$label)
  })
  list(id = sprintf("sd20-%02d", i), text = t, spans = spans)
})
out <- list(
  name = "sd20",
  source = paste("structured_deidentification LLM A/B gold set (ab_llm.R, 2026-09-06),",
                 "rebuilt by eval/bench/make_sd20.R; synthetic"),
  offsets = "1-based, end inclusive",
  docs = docs)
jsonlite::write_json(out, file.path(here, "sd20.json"), auto_unbox = TRUE, pretty = TRUE)
cat("wrote", length(docs), "notes,", sum(lengths(lapply(docs, `[[`, "spans"))), "gold spans\n")

# slm_jev

This project is an offline extractor for **PII and sensitive health information (SHI)** in
Singapore-context data. It uses a small local language model and a *Jev-style* judgment layer. The
layer answers typed questions with calibrated probabilities:
- yes/no: does this span identify a person?
- which of the 15 SingHealth identifiers or SHI categories is it?
- how sensitive is it?

Status: **P1.** The Singapore rule detectors are ported to `slmjev/rules.py`, with R-parity tests. See [CLAUDE.md](CLAUDE.md) for the design, constraints and roadmap.

- Runs fully offline. Nothing leaves the machine.
- Only synthetic data is kept in this repository.
- Research and governance tool; not for clinical or diagnostic use.

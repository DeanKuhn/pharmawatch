# PharmaWatch

Drug safety signal platform built on FDA adverse event data (FAERS/openFDA).

See CLAUDE.md for architecture and phase plan.

## Mess log

Data quality issues discovered in FAERS/openFDA, with examples. Updated as we find them.

### FAERS quarterly extract filename prefix changed at 2013q1

Files for 2004q1 through 2012q4 are named `aers_ascii_{quarter}.zip`; 2013q1 onward are
named `faers_ascii_{quarter}.zip`. Root cause: FDA renamed the underlying system from
AERS (Adverse Event Reporting System) to FAERS around that time, and the extract file
naming carried the rebrand. Undocumented on the download page itself — only visible by
looking at the actual file list.

Example: `aers_ascii_2012q4.zip` vs. `faers_ascii_2013q1.zip`.

`src/faers/download.py`'s `_filename_for_quarter()` picks the right prefix based on year
(the cutover happens to land exactly on a year boundary, so `year < 2013` is sufficient —
this would need revisiting if a future undocumented quirk didn't align so cleanly).

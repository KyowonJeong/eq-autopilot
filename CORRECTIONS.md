# Corrections

Where a statement in this repository turned out to be wider than the facts, we record the correction
here rather than removing the original. Entries are not deleted. Korean version: `CORRECTIONS.ko.md`.

## 2026-09-23 — commit message of `a923405` said "member installations never send market data"

`a923405` added the NinjaTrader backup-bar feature. Its commit message ends a sentence with
"member installations never send market data".

Inside that feature the statement holds. The bridge path forwards bars only when the running token is
the operator's own, so a member installation never feeds it, and the README states that narrow version.
Read on its own, however, the sentence is wider than the facts: **a member's app does send execution
details, including fill prices, through the separate fill-quality report.** That report is how we measure
and publish the gap between a published signal and what accounts actually filled.

The accurate scope is therefore: *the backup-bar path never carries bars from a member installation.*
It is not a statement about everything a member installation sends. What the application sends is listed
in the README and in the privacy policy, and those lists are the reference, not a commit message.

We are leaving the commit message in place. A repository whose history can be edited is worth less as
evidence than one that carries its own corrections, and this project asks to be checked rather than
believed.

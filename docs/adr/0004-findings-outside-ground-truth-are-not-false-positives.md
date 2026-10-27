# A finding that misses the known bug is not automatically a false positive

CyberGym-E2E knows one vulnerability per task. A system can report a different, real bug, and scoring against `patch.diff` alone would count it as a false positive. Since false positives and system mistakes are what the study is about, that would inflate exactly the number we report. Every finding therefore gets one of four verdicts: `tp_target` (the known bug), `tp_other` (a different real bug, shown by a PoC crash at another location or by two coders agreeing), `fp` (two coders agree it is not a bug), or `unverified`.

**Consequences:** findings outside the ground truth need manual adjudication, which costs coder time. The share of `unverified` findings must be reported.

# Claude Haiku 5.5 is the single study model

We planned to run every role of every system on Claude Sonnet 5.5 (P4). In the ticket 01 data run, Sonnet 5.5 refused CyberGym-E2E's end-to-end task on every call (`stop_reason: refusal`, category `cyber`), while Claude Haiku 5.5 completed the same task. Anthropic's route for legitimate security work, the Cyber Verification Program, applies to the team's own Anthropic organization, and our calls go through OpenRouter under its account. We chose Haiku 5.5 for every system and role, development and data runs alike, rather than wait for verification and a bring-your-own-key setup, or move to another provider, which would drop the Claude-only Anthropic harness.

**Consequences:** failure modes are those of a smaller model and may not carry over to larger ones; the paper must say so. Haiku 5.5 can also refuse, so M-2 still applies. Switching models later means re-running every data run.

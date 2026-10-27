# Trajectory bundles, not Phoenix, are the source of truth for coding

The paper says we store traces in Arize Phoenix, and we do, but human coders, the LLM judge, and the released dataset all read a per-run trajectory bundle instead: step records, captured state, oracle results, and a readable rendering, built by our exporter from the raw proxy log, native traces, and collected artifacts. Coding labels live in versioned files keyed by step ID, one per coder. Phoenix knows nothing about stages, hand-offs, or captured state, and codes stored in a tool's database are hard to version, compute κ over, or release. Phoenix stays the tool for exploring and debugging runs.

**Consequences:** the exporter is load-bearing. A field missing from the bundle is missing from the analysis, even if Phoenix shows it.

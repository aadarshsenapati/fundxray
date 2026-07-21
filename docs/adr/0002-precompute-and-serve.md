# ADR 0002 — Precompute and serve

**Status:** accepted

**Context.** The product must answer arbitrary user portfolio queries instantly, at zero infrastructure cost, over a dataset that requires distributed compute to produce.

**Decision.** Run all heavy computation offline on a schedule; publish a compacted DuckDB/Parquet artifact to object storage; serve from a stateless API that loads the artifact into memory.

**Rationale.** The space of *fund-level* answers is small and precomputable even though the input data is large. User portfolios are just weighted combinations of precomputed fund vectors, resolvable in milliseconds.

**Consequences.** Freshness is bounded by the artifact rebuild cadence — acceptable, since disclosures are monthly. Live prices are layered on separately via the speed plane.

# ADR 0003 — ISIN as the canonical instrument key

**Status:** accepted

**Context.** The same company appears under many name spellings across AMCs and across years; companies also merge, demerge and rename.

**Decision.** Resolve every holding to ISIN first. Where ISIN is absent (common in older files), use blocked fuzzy name matching against a reference table, and record the resolution method and confidence on the row.

**Rationale.** Name matching alone is unreliable at this scale; ISIN is stable, regulator-backed and present in most modern disclosures.

**Consequences.** Requires a maintained ISIN-to-entity mapping with history, and a hand-labelled golden set to measure match rate. Unresolved holdings are quarantined and counted — the match rate is a published quality metric, not a hidden one.

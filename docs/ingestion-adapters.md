# Ingestion Adapters

~45 AMCs publish SEBI-mandated monthly portfolio disclosures in their own Excel and PDF layouts, which have changed repeatedly over the past decade. This is the hardest part of the project and the reason the dataset does not already exist.

## Adapter contract

Every adapter implements a common interface and returns rows conforming to the canonical holdings schema in `core/fundxray_core/schemas/`.

```
class AMCAdapter(Protocol):
    amc_code: str
    def sniff(self, path: Path) -> float:      # confidence this adapter fits the file
    def parse(self, path: Path) -> Iterator[RawHolding]
```

## Resolution strategy

1. **Fingerprint** the file — extension, sheet names, header row signature, column count.
2. **Score** every registered adapter via `sniff()`; pick the highest confidence above threshold.
3. **Fail loudly** if no adapter matches. An unrecognised format is a quarantine event and an alert, never a silent skip.

## Known failure modes

- Header rows that shift between years
- Merged cells and multi-row headers in Excel
- Scheme sections stacked in one sheet with no delimiter
- Footnote rows that look like holdings
- Percentage columns stored as text, sometimes with a `%` suffix
- ISIN column absent in older files, requiring name-based resolution
- PDF-only disclosures from smaller AMCs
- Numbers in lakhs vs. crores, inconsistently labelled

## Testing

Every adapter ships with a fixture file in `tests/fixtures/` and a golden expected output. Property-based tests assert invariants that must hold for all adapters — weights sum to ~100%, no negative holdings, every ISIN well-formed.

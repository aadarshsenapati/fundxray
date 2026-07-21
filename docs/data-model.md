# Data Model

## Canonical holdings row (silver)

| Column | Type | Notes |
|---|---|---|
| scheme_code | string | AMFI scheme code |
| amc_code | string | |
| disclosure_month | date | partition key |
| isin | string | canonical instrument identifier |
| company_id | string | resolved entity, stable across renames |
| instrument_name_raw | string | as printed in the source file |
| quantity | decimal | corporate-action adjusted |
| market_value | decimal | INR |
| weight_pct | decimal | share of scheme net assets |
| asset_class | string | equity / debt / cash / derivative |
| source_file | string | provenance |
| ingestion_run_id | string | provenance |
| parsed_at | timestamp | |

Partitioned by `disclosure_month`, clustered by `scheme_code`.

## Reference tables

- `dim_scheme` — scheme metadata, category, benchmark, TER (direct and regular), plan type
- `dim_company` — resolved entities, ISIN history, listing status
- `dim_cap_classification` — AMFI large/mid/small list, **effective-dated** (half-yearly)
- `fact_nav` — daily NAV, entire industry
- `fact_price` — daily OHLCV from SmartAPI
- `dim_corporate_action` — splits, bonuses, mergers

## Gold marts

`mart_overlap`, `mart_active_share`, `mart_style_drift`, `mart_turnover`, `mart_crowding`, `mart_dtl`, `mart_scheme_profile`.

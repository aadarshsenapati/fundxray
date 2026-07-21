from __future__ import annotations

from dagster import Definitions, ScheduleDefinition, define_asset_job

from . import assets

monthly_job = define_asset_job(
    "monthly_ingestion",
    selection=["bronze_holdings", "silver_holdings", "quality_report"],
    partitions_def=assets.MONTHLY,
)
publish_job = define_asset_job("publish", selection=["gold_marts", "serving_artifact"])

defs = Definitions(
    assets=[assets.bronze_holdings, assets.silver_holdings, assets.quality_report,
            assets.gold_marts, assets.serving_artifact],
    jobs=[monthly_job, publish_job],
    schedules=[
        # AMCs publish within ~10 days of month end.
        ScheduleDefinition(job=monthly_job, cron_schedule="0 3 12 * *"),
        ScheduleDefinition(job=publish_job, cron_schedule="0 5 12 * *"),
    ],
)

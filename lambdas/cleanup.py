import json
from typing import Any

from _types import DomainInput
from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)
from io_operations.s3_client import S3Client
from logger import logger

s3 = S3Client()


def handler(event: dict, context: Any) -> dict:
    workflow_mode = event.get("workflow_mode", "regular")
    job_id = event.get("job_id", "")

    if not job_id:
        logger.error("No job_id in event")
        return {"saved": 0, "deleted": 0}

    strategy = _create_strategy(workflow_mode)

    processed, failed, staging_keys = _read_staged_results(workflow_mode, job_id)

    if processed or failed:
        strategy.save_results(processed, failed)

    to_delete = _resolve_records_to_delete(processed, workflow_mode, event)
    if workflow_mode == "production" and to_delete:
        strategy.on_complete(to_delete, job_id=job_id)

    # TODO: Until we are in testing phase we won't delete these.
    # _cleanup_staging(staging_keys)

    logger.info(
        f"Job {job_id}: {len(processed)} saved, {len(to_delete)} deleted, {len(failed)} failed"
    )
    return {"saved": len(processed), "deleted": len(to_delete)}


def _create_strategy(workflow_mode: str):
    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    return (
        ProductionSheetStrategy(handler_)
        if workflow_mode == "production"
        else RegularSheetStrategy(handler_)
    )


def _read_staged_results(workflow_mode: str, job_id: str) -> tuple[list, list, list]:
    processed, failed = [], []
    keys = s3.list_objects(f"staging/{workflow_mode}/{job_id}/")
    for key in keys:
        content = s3.read_content(key)
        if content is None:
            continue
        try:
            data = json.loads(content)
            processed.extend(data.get("processed", []))
            failed.extend(data.get("failed", []))
        except Exception as e:
            logger.error(f"Failed to parse s3://{s3.bucket_name}/{key}: {e}")
    return processed, failed, keys


def _resolve_records_to_delete(
    processed: list, workflow_mode: str, event: dict
) -> list[DomainInput]:
    to_delete = [DomainInput(row_no=r["row_no"], company_url=r["domain"]) for r in processed]
    if workflow_mode == "production":
        to_delete.extend(
            DomainInput(row_no=r["row_no"], company_url=r["domain"])
            for r in event.get("skipped_records", [])
        )
    return to_delete


def _cleanup_staging(keys: list[str]) -> None:
    if keys:
        s3.delete_objects(keys)
        logger.info(f"Cleaned up {len(keys)} staging files")

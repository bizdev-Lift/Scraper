from typing import Any

from _types import DomainInput
from config import settings
from io_operations.google_sheets import GoogleSheetsHandler, ProductionSheetStrategy
from logger import logger


def handler(event: dict, context: Any) -> dict:
    """Delete successfully processed domains from the input sheet."""
    workflow_mode = event.get("workflow_mode", "regular")
    if workflow_mode != "production":
        logger.info("Cleanup skipped — only runs in production mode")
        return {"deleted": 0}

    results = event.get("results", event.get("payload", {}).get("results", []))
    if not results:
        return {"deleted": 0}

    all_processed = []
    for worker_result in results:
        all_processed.extend(worker_result.get("processed", []))

    if not all_processed:
        logger.info("No successfully processed domains to clean up")
        return {"deleted": 0}

    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    strategy = ProductionSheetStrategy(handler_)
    records = [
        DomainInput(row_no=item["row_no"], company_url=item["domain"]) for item in all_processed
    ]
    strategy.on_complete(records)

    logger.info(f"Cleanup complete: {len(records)} rows deleted")
    return {"deleted": len(records)}

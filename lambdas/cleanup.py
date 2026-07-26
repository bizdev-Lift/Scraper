from typing import Any

from config import settings
from io_operations.google_sheets import GoogleSheetsHandler
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
    sheet = handler_.open_sheet(settings.spreadsheet_info.sheet_name)

    row_numbers = sorted(set(item["row_no"] for item in all_processed), reverse=True)
    for row_no in row_numbers:
        try:
            sheet.delete_rows(row_no)
            logger.info(f"Deleted row {row_no} from input sheet")
        except Exception as e:
            logger.error(f"Failed to delete row {row_no}: {e}")

    logger.info(f"Cleanup complete: {len(row_numbers)} rows deleted")
    return {"deleted": len(row_numbers)}

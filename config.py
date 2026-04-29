import os
from _types import GoogleSheetInfo
from typing import Final


class Settings:
    max_input_records: int = int(os.getenv("MAX_INPUT_RECORDS", 5))
    sheet_start_row: int = 2
    global_http_timeout: Final[float] = 5.0
    environment = os.environ.get("ENVIRONMENT", "prod")
    google_authorization_scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    hubspot_api_key: str = os.environ["HUBSPOT_API_KEY"]
    spreadsheet_info: GoogleSheetInfo = GoogleSheetInfo(
        spreadsheet_id=os.environ["SPREADSHEET_ID"],
        sheet_name=os.environ["SHEET_NAME"],
        credentials_path=os.environ["CREDENTIALS_PATH"],
        stats_spreadsheet_id=os.environ["STATS_SPREADSHEET_ID"],
        stats_sheet=os.environ["STATS_SHEET"],
        good_results_sheet=os.environ.get("GOOD_RESULTS_SHEET"),
        skip_results_sheet=os.environ.get("SKIP_RESULTS_SHEET"),
        error_results_sheet=os.environ.get("ERROR_RESULTS_SHEET"),
        history_good_results_sheet=os.environ.get("HISTORY_GOOD_RESULTS_SHEET"),
        history_skip_results_sheet=os.environ.get("HISTORY_SKIP_RESULTS_SHEET"),
        history_error_results_sheet=os.environ.get("HISTORY_ERROR_RESULTS_SHEET"),
        hubspot_database_sheet=os.environ.get("HUBSPOT_DATABASE_SHEET"),

    )
    proxy = None
    gemini_api_key = os.environ["GEMINI_API_KEY"]
    zyte_enabled: bool = os.getenv("ZYTE_ENABLED", "false").lower() == "true"
    zyte_url: str = "https://api.zyte.com/v1/extract"
    zyte_api_key: str = os.environ["ZYTE_API_KEY"]


settings = Settings()

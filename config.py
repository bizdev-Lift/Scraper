import os
from typing import Any, Final

from _types import GoogleSheetInfo


class Settings:
    def __init__(self) -> None:
        self._validate_env()
        self.max_input_records: int = int(os.getenv("MAX_INPUT_RECORDS", 5))
        self.sheet_start_row: int = 2
        self.global_http_timeout: Final[float] = 5.0
        self.environment: str = os.environ.get("ENVIRONMENT", "prod")
        self.google_authorization_scope: list[str] = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        self.hubspot_api_key: str = os.environ["HUBSPOT_API_KEY"]
        self.spreadsheet_info: GoogleSheetInfo = GoogleSheetInfo(
            spreadsheet_id=os.environ["SPREADSHEET_ID"],
            sheet_name=os.environ["SHEET_NAME"],
            credentials_path=os.environ["CREDENTIALS_PATH"],
            stats_spreadsheet_id=os.environ["STATS_SPREADSHEET_ID"],
            stats_sheet=os.environ["STATS_SHEET"],
            good_results_sheet=os.environ.get("GOOD_RESULTS_SHEET"),
            skip_results_sheet=os.environ.get("SKIP_RESULTS_SHEET"),
            error_results_sheet=os.environ.get("ERROR_RESULTS_SHEET"),
            single_spreadsheet_id=os.environ.get("SINGLE_SPREADSHEET_ID"),
            single_sheet_name=os.environ.get("SINGLE_SHEET_NAME"),
        )
        self.proxy: Any = None
        self.gemini_api_key: str = os.environ["GEMINI_API_KEY"]
        self.zyte_enabled: bool = os.getenv("ZYTE_ENABLED", "false").lower() == "true"
        self.zyte_url: str = "https://api.zyte.com/v1/extract"
        self.zyte_api_key: str = os.environ["ZYTE_API_KEY"]
        self.apollo_api_key: str = os.environ["APOLLO_API_KEY"]
        self.seamless_api_key: str = os.environ["SEAMLESS_API_KEY"]
        self.ahrefs_api_key: str = os.environ["AHREFS_API_KEY"]
        self.model_name: str = os.environ["MODEL_NAME"]
        self.override_model_name: str = os.environ.get("OVERRIDE_MODEL_NAME")
        self.valid_domains: list[str] = ["com", "us"]

    def _validate_env(self) -> None:
        required_vars = [
            "HUBSPOT_API_KEY",
            "SPREADSHEET_ID",
            "SHEET_NAME",
            "CREDENTIALS_PATH",
            "STATS_SPREADSHEET_ID",
            "STATS_SHEET",
            "GEMINI_API_KEY",
            "ZYTE_API_KEY",
            "APOLLO_API_KEY",
            "SEAMLESS_API_KEY",
        ]
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings()

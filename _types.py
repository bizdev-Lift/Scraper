from dataclasses import dataclass
from typing import Literal


@dataclass
class GoogleSheetInfo:
    spreadsheet_id: str
    sheet_name: str
    credentials_path: str
    stats_spreadsheet_id: str
    stats_sheet: str
    good_results_sheet: str | None = None
    skip_results_sheet: str | None = None
    error_results_sheet: str | None = None
    history_good_results_sheet: str | None = None
    history_skip_results_sheet: str | None = None
    history_error_results_sheet: str | None = None
    hubspot_database_sheet: str | None = None

@dataclass
class DomainInput:
    row_no: int
    company_url: str
    company_name: str | None = None
    old_lead_status: str | None = None


@dataclass
class DomainResponse:
    hq_phone_no: str
    website_availability: Literal["Yes", "No"]
    hq_address_listed: Literal["Yes", "No"]
    b2c_sales: Literal["Yes", "No"]
    b2b_sales: Literal["Yes", "No"]
    industry_classification: str | None
    ecommerce_platform: str | None
    lead_status: str | None = None
    is_us_based: str | None = None
    bad_product_type: str | None = None
    revenue: float | None = None
    redirected_to: str | None = None
    old_lead_status: str | None = None

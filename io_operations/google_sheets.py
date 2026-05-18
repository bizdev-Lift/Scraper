import gspread
import logging
import copy
import re
import datetime

from oauth2client.service_account import ServiceAccountCredentials
from abc import ABC, abstractmethod

from _types import GoogleSheetInfo, DomainInput, DomainResponse
from config import settings
from hubspot.records_api import HubSpotCompaniesClient

logger = logging.getLogger(__name__)


class GoogleSheetsHandler:
    """Handles only authentication and opening sheets. Shared by both modes."""

    def __init__(self, spreadsheet_info: GoogleSheetInfo):
        self.spreadsheet_info = spreadsheet_info
        self.client: gspread.Client = self._authenticate()
        self.stats_sheet = self.open_sheet(
            spreadsheet_info.stats_sheet,
            spreadsheet_info.stats_spreadsheet_id,
        )

    def _authenticate(self) -> gspread.Client:
        if not self.spreadsheet_info.credentials_path:
            raise ValueError(
                "Credentials not found for authenticating to Google Sheets!"
            )

        creds = ServiceAccountCredentials.from_json_keyfile_name(
            self.spreadsheet_info.credentials_path, settings.google_authorization_scope
        )
        try:
            return gspread.authorize(creds)
        except Exception as e:
            logger.error(f"{e.__class__.__name__}: Error authorizing service account!")
            raise

    def open_sheet(
        self, sheet_name: str, sheet_id: str | None = None
    ) -> gspread.Worksheet:
        try:

            return self.client.open_by_key(
                self.spreadsheet_info.spreadsheet_id if not sheet_id else sheet_id
            ).worksheet(sheet_name)
        except Exception:
            logger.error(
                f"Unable to load sheet '{sheet_name}'. Check spreadsheet_id and sheet_name."
            )
            raise

    @staticmethod
    def is_domain(input_string: str) -> bool:
        domain_pattern = (
            r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
        )
        return bool(re.match(domain_pattern, input_string))

    def update_stats(self, record: list[str]):
        self.stats_sheet.append_row(
            [*record, datetime.datetime.now().strftime("%m-%d-%Y")],
            table_range="A:F",
        )


class BaseSheetStrategy(ABC):
    """Defines the interface that MainExecutor depends on."""

    @abstractmethod
    def get_records(self) -> list[DomainInput]: ...

    @abstractmethod
    def on_success(self, record: DomainInput, output: DomainResponse) -> None: ...

    @abstractmethod
    def on_error(self, record: DomainInput, output: DomainResponse) -> None: ...

    @abstractmethod
    def on_complete(self, records: list[DomainInput]) -> None: ...

    def get_unique_records(self, records: list) -> list:
        seen = set()
        unique = []
        for item in records:
            t = tuple(item)
            if t not in seen:
                seen.add(t)
                unique.append(item)

        return unique


class RegularSheetStrategy(BaseSheetStrategy):
    """Single-sheet: reads and updates records in place."""

    def __init__(self, handler: GoogleSheetsHandler):
        self.sheet = handler.open_sheet(handler.spreadsheet_info.sheet_name)

    def get_records(self) -> list[DomainInput]:
        values = self.get_unique_records(self.sheet.get_all_values("A:J")[1:])
        records = []
        count = copy.deepcopy(settings.max_input_records)
        logger.info(f"Total records to process: {count}")

        for index, record in enumerate(values, start=settings.sheet_start_row):
            if count == 0:
                break
            if not record[0] or not record[1]:
                continue
            if record[3] != "" and "LLM Failed" not in record[-1]:
                continue

            records.append(
                DomainInput(
                    row_no=index,
                    company_name=record[0],
                    company_url=record[1],
                    old_lead_status=record[-1],
                )
            )
            count -= 1

        return records

    def on_success(self, record: DomainInput, output: DomainResponse) -> None:
        self._update(record, output)

    def on_error(self, record: DomainInput, output: DomainResponse) -> None:
        self._update(record, output)

    def on_complete(self, records: list[DomainInput]) -> None:
        pass  # No cleanup needed in regular mode

    def _update(
        self,
        domain_input: DomainInput,
        output: DomainResponse,
        is_new_record: bool = False,
    ) -> None:
        row = [
            output.hq_phone_no,
            output.website_availability,
            output.hq_address_listed,
            output.b2c_sales,
            output.b2b_sales,
            output.industry_classification,
            output.ecommerce_platform,
            output.lead_status,
            output.revenue,
            output.shipping_messaging,
            output.shipping_methods,
            output.carriers,
            output.product_size_weight,
            output.redirected_to,
            output.old_lead_status,
        ]
        if is_new_record:
            self.sheet.append_row(
                [domain_input.company_name, domain_input.company_url, *row],
                table_range="A:Q",
            )
        else:
            self.sheet.update([row], f"C{domain_input.row_no}:Q{domain_input.row_no}")


class ProductionSheetStrategy(BaseSheetStrategy):
    """Multi-sheet: routes records to good/error/skip sheets and cleans up input."""

    def __init__(self, handler: GoogleSheetsHandler):
        self.handler = handler
        info = handler.spreadsheet_info
        self.input_sheet = handler.open_sheet(info.sheet_name)
        self.good_sheet = handler.open_sheet(info.good_results_sheet)
        self.skip_sheet = handler.open_sheet(info.skip_results_sheet)
        self.error_sheet = handler.open_sheet(info.error_results_sheet)
        self.history_domains = self._load_history_domains()

    def get_records(self) -> list[DomainInput]:
        records = []
        records_to_check = []
        count = copy.deepcopy(settings.max_input_records)
        unique_input_domains = self.get_unique_records(self.input_sheet.get_all_values("A:B")[1:])
        for index, record in enumerate(unique_input_domains, start=2):
            if count == 0:
                break
            if record[0] and GoogleSheetsHandler.is_domain(record[0]):
                records.append(DomainInput(row_no=index, company_url=record[0]))
                records_to_check.append(record[0])
            count -= 1

        hubspot_client = HubSpotCompaniesClient(settings.hubspot_api_key)
        self.history_domains = self.history_domains.union(
            hubspot_client.get_existing_domains(records_to_check)
        )
        return records

    def is_seen(self, record: DomainInput) -> bool:
        return record.company_url.lower() in self.history_domains

    def on_skip(self, record: DomainInput) -> None:
        self.skip_sheet.append_row([record.company_url], table_range="A:B")

    def on_success(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.good_sheet, record, output)

    def on_error(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.error_sheet, record, output)

    def on_complete(self, records: list[DomainInput]) -> None:
        row_numbers = sorted([r.row_no for r in records], reverse=True)
        logger.info(f"Total rows to delete: {len(row_numbers)}")
        for row_no in row_numbers:
            self.input_sheet.delete_rows(row_no)

    def _append(
        self,
        sheet: gspread.Worksheet,
        domain_input: DomainInput,
        output: DomainResponse,
    ) -> None:
        sheet.append_row(
            [
                domain_input.company_url,
                output.hq_phone_no,
                output.website_availability,
                output.hq_address_listed,
                output.b2c_sales,
                output.b2b_sales,
                output.industry_classification,
                output.ecommerce_platform,
                output.lead_status,
                output.revenue,
                output.shipping_messaging,
                output.shipping_methods,
                output.carriers,
                output.product_size_weight,
                output.redirected_to,
                datetime.datetime.now().strftime("%m-%d-%Y"),
            ],
            table_range="A:P",
        )

    def _load_history_domains(self) -> set[str]:
        info = self.handler.spreadsheet_info
        sheet_names = [
            info.history_good_results_sheet,
            info.history_skip_results_sheet,
            info.history_error_results_sheet,
        ]
        domains = set()
        for sheet_name in sheet_names:
            sheet = self.handler.open_sheet(sheet_name)
            for record in sheet.get_all_values("A:B")[1:]:
                if record[0] and GoogleSheetsHandler.is_domain(record[0]):
                    domains.add(record[0].lower())
        return domains

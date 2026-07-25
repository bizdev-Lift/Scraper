import gspread
import logging
import copy
import re
import datetime
from typing import Literal
from oauth2client.service_account import ServiceAccountCredentials
from abc import ABC, abstractmethod
from dataclasses import asdict
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
            table_range="A1",
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
            # output.product_dimensions,
            # output.single_product_dim,
            # output.single_product_cubic_size,
            # output.single_product_name,
            output.smallest_product_dim,
            output.smallest_product_cubic_size,
            output.smallest_product_name,
            output.largest_product_dim,
            output.largest_product_cubic_size,
            output.largest_product_name,
            output.redirected_to,
            output.old_lead_status,
        ]
        if is_new_record:
            self.sheet.append_row(
                [domain_input.company_name, domain_input.company_url, *row],
                table_range="A1"
            )
        else:
            self.sheet.update([row], f"C{domain_input.row_no}")


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
        self.hubspot_client = HubSpotCompaniesClient(settings.hubspot_api_key)

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

        self.history_domains = self.history_domains.union(
            self.hubspot_client.get_existing_domains(records_to_check)
        )
        return records

    def is_seen(self, record: DomainInput) -> bool:
        return record.company_url.lower() in self.history_domains

    def on_skip(self, record: DomainInput) -> None:
        self._append(self.skip_sheet, record, [record.company_url], status="skip")

    def on_success(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.good_sheet, record, output)

    def on_error(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.error_sheet, record, output, status="error")

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
        status: Literal["success", "skip", "error"] = "success"
    ) -> None:
        scrape_date = datetime.datetime.now().strftime("%m-%d-%Y")
        hubspot_scrape_date = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        hubspot_scrape_date = hubspot_scrape_date.replace(hour=0, minute=0, second=0, microsecond=0)
        hubspot_scrape_date = str(int(hubspot_scrape_date.timestamp()*1000))
    
        def get_string(value):
            if not value:
                return ""
            
            return value
            
        if status == "success":
            record = {
                "domain": domain_input.company_url,
                "properties": {
                    "name": domain_input.company_url,
                    "website": domain_input.company_url,
                    "confirmed_website___headquarters_phone__": get_string(output.hq_phone_no),
                    "is_website_live_": get_string(output.website_availability),
                    "address_listed_on_website_": get_string(output.hq_address_listed),
                    "do_the_sell_b2c": output.b2c_sales.lower(),
                    "do_they_sell_b2b": output.b2b_sales.lower(),
                    "industry_type_verified": output.industry_classification.lower(),
                    "ecommerce_platform": get_string(output.ecommerce_platform),
                    "scraper_results": get_string(output.lead_status),
                    "annual_revenue_scraper": get_string(output.revenue),
                    "scraper_shipping_messages": get_string(output.shipping_messaging),
                    "scraper_shipping_methods": get_string(output.shipping_methods),
                    "scraper_carriers": get_string(output.carriers),
                    "scraper_product_size": get_string(output.product_size_weight),
                    "hs_redirect_domain": get_string(output.redirected_to),
                    "apollo_industry": get_string(output.apollo_result.industry),
                    "hq_phone_number_apollo": get_string(output.apollo_result.company_phone),
                    "apollo___of_retail_locations": get_string(output.apollo_result.company_state),
                    "apollo_annual_revenue_number_fix_use_this": get_string(output.apollo_result.annual_revenue),
                    "employee_count_seamless": get_string(output.seamless_result.num_of_employees),
                    "annual_revenue_seamless": get_string(output.seamless_result.annual_revenue) ,
                    'scraper_smallest_product_cubic': get_string(output.smallest_product_cubic_size), 
                    'scraper_product_dimensions': get_string(output.smallest_product_dim),
                    'scraper_smallest_product_name': get_string(output.smallest_product_name),
                    'scraper_largest_product_cubic': get_string(output.largest_product_cubic_size),
                    'scraper_largest_product_dimensions': get_string(output.largest_product_dim),
                    'scraper_largest_product_name': get_string(output.largest_product_name),
                    "scrape_date": hubspot_scrape_date,                    
                }
            }
            result = self.hubspot_client.add_company(record)
            if not result['success']:
                logger.info(result)
                raise Exception(f"Error while saving domain {domain_input.company_url} to HubSpot!!.")
            
            record = [
                domain_input.company_url,
                domain_input.company_url,
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
                output.smallest_product_dim,
                output.smallest_product_cubic_size,
                output.smallest_product_name,
                output.largest_product_dim,
                output.largest_product_cubic_size,
                output.largest_product_name,
                output.redirected_to,
                *list(asdict(output.apollo_result).values()),
                *list(asdict(output.seamless_result).values()),
                scrape_date
            ]
            sheet.append_row(record, table_range="A1")
            
        elif status == "error":
            record = [
                domain_input.company_url,
                output.lead_status,
                scrape_date
            ]
            sheet.append_row(record, table_range="A1")

        elif status == "skip":
            record = [domain_input.company_url, scrape_date]
            sheet.append_row(record, table_range="A1")
        else:
            logger.error(f"Invalid scrape status. Not saving anything in google sheets for {domain_input.company_url}")


    def _load_history_domains(self) -> set[str]:
        info = self.handler.spreadsheet_info
        sheet_names = [
            info.good_results_sheet,
            info.skip_results_sheet,
            info.error_results_sheet,
        ]
        domains = set()
        for sheet_name in sheet_names:
            sheet = self.handler.open_sheet(sheet_name)
            for record in sheet.get_all_values("A:B")[1:]:
                if record[0] and GoogleSheetsHandler.is_domain(record[0]):
                    domains.add(record[0].lower())
        return domains

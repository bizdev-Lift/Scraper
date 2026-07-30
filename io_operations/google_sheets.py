import copy
import datetime
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, Literal, Optional

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from _types import DomainInput, DomainResponse, GoogleSheetInfo
from config import settings
from hubspot.records_api import HubSpotCompaniesClient

logger = logging.getLogger(__name__)


class GoogleSheetsError(Exception):
    """Custom exception for Google Sheets operations."""

    pass


class GoogleSheetsHandler:
    def __init__(self, spreadsheet_info: GoogleSheetInfo):
        self.spreadsheet_info = spreadsheet_info
        self.client: gspread.Client = self._authenticate()
        self.stats_sheet = self.open_sheet(
            spreadsheet_info.stats_sheet,
            spreadsheet_info.stats_spreadsheet_id,
        )

    def _authenticate(self) -> gspread.Client:
        if not self.spreadsheet_info.credentials_path:
            raise GoogleSheetsError("Credentials path not provided.")
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self.spreadsheet_info.credentials_path, settings.google_authorization_scope
            )
            client = gspread.authorize(creds)
            return client
        except Exception as e:
            logger.error(f"Failed to authorize service account: {e}")
            raise GoogleSheetsError("Service account authorization failed.") from e

    def open_sheet(self, sheet_name: str, sheet_id: Optional[str] = None) -> gspread.Worksheet:
        count = 3
        while count > 0:
            try:
                spreadsheet_id = sheet_id or self.spreadsheet_info.spreadsheet_id
                return self.client.open_by_key(spreadsheet_id).worksheet(sheet_name)
            except Exception as e:
                logger.error(f"Unable to load sheet '{sheet_name}': {e}")
                logger.error("waiting for 5 seconds")
                time.sleep(random.randint(0, 10))
                logger.error("Trying now")
                count -= 1
                if count == 0:
                    raise GoogleSheetsError(f"Failed to load sheet: {sheet_name}") from e

    @staticmethod
    def is_domain(input_string: str) -> bool:
        return bool(
            re.match(
                r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$", input_string
            )
        )

    def update_stats(self, record: list[str]) -> None:
        try:
            self.stats_sheet.append_row(
                [*record, datetime.datetime.now().strftime("%m-%d-%Y")],
                table_range="A1",
            )
        except Exception as e:
            logger.error(f"Failed to update stats: {e}")


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

    @abstractmethod
    def save_results(self, processed: list[dict], failed: list[dict]) -> None: ...

    @abstractmethod
    def build_response(self, job_id: str) -> dict: ...

    @property
    def pre_enrich(self) -> bool:
        return False

    @property
    def check_seen(self) -> bool:
        return False

    @property
    def track_old_lead_status(self) -> bool:
        return False

    def is_seen(self, record: DomainInput) -> bool:
        return False

    def on_skip(self, record: DomainInput) -> None:
        pass

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

    @property
    def pre_enrich(self) -> bool:
        return True

    @property
    def track_old_lead_status(self) -> bool:
        return True

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

    def save_results(self, processed: list[dict], failed: list[dict]) -> None:
        scrape_date_str = datetime.datetime.now().strftime("%m-%d-%Y")
        cells = []
        items = processed + failed
        for item in items:
            data = item.get("data", {})
            apollo = data.get("apollo_result", {})
            seamless = data.get("seamless_result", {})
            row = [
                data.get("hq_phone_no", ""),
                data.get("website_availability", ""),
                data.get("hq_address_listed", ""),
                data.get("b2c_sales", ""),
                data.get("b2b_sales", ""),
                data.get("industry_classification", ""),
                data.get("ecommerce_platform", ""),
                data.get("lead_status", ""),
                data.get("revenue", ""),
                data.get("shipping_messaging", ""),
                data.get("shipping_methods", ""),
                data.get("carriers", ""),
                data.get("product_size_weight", ""),
                data.get("smallest_product_dim", ""),
                data.get("smallest_product_cubic_size", ""),
                data.get("smallest_product_name", ""),
                data.get("largest_product_dim", ""),
                data.get("largest_product_cubic_size", ""),
                data.get("largest_product_name", ""),
                data.get("redirected_to", ""),
                data.get("old_lead_status", ""),
                *apollo.values(),
                *seamless.values(),
                scrape_date_str,
            ]
            for i, val in enumerate(row):
                cells.append(gspread.Cell(item["row_no"], i + 3, val))
        if cells:
            self.sheet.batch_update(cells)
        time.sleep(0.5)

    def on_complete(self, records: list[DomainInput]) -> None:
        pass  # No cleanup needed in regular mode

    def build_response(self, job_id: str) -> dict:
        records = self.get_records()
        CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "5"))
        chunks = []
        for i in range(0, len(records), CHUNK_SIZE):
            chunk_records = records[i : i + CHUNK_SIZE]
            chunks.append(
                {
                    "chunk_id": i // CHUNK_SIZE,
                    "domains": [
                        {"domain": r.company_url, "row_no": r.row_no} for r in chunk_records
                    ],
                    "workflow_mode": "regular",
                    "job_id": job_id,
                }
            )
        logger.info(f"Split {len(records)} records into {len(chunks)} chunks (job_id={job_id})")
        return {"workflow_mode": "regular", "job_id": job_id, "chunks": chunks}

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
                [domain_input.company_name, domain_input.company_url, *row], table_range="A1"
            )
        else:
            self.sheet.update([row], f"C{domain_input.row_no}")
        time.sleep(0.5)


class ProductionSheetStrategy(BaseSheetStrategy):
    """Multi-sheet: routes records to good/error/skip sheets and cleans up input."""

    def __init__(self, handler: GoogleSheetsHandler):
        self.handler = handler
        info = handler.spreadsheet_info
        self.input_sheet = handler.open_sheet(info.sheet_name)
        self.good_sheet = handler.open_sheet(info.good_results_sheet)
        self.skip_sheet = handler.open_sheet(info.skip_results_sheet)
        self.error_sheet = handler.open_sheet(info.error_results_sheet)
        self.hubspot_client = HubSpotCompaniesClient(settings.hubspot_api_key)

    @property
    def pre_enrich(self) -> bool:
        return True

    @property
    def check_seen(self) -> bool:
        return True

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

        history_domains = self._load_history_domains()
        history_domains = history_domains.union(
            self.hubspot_client.get_existing_domains(records_to_check)
        )

        unseen = []
        seen = []
        for r in records:
            if r.company_url.lower() not in history_domains:
                unseen.append(r)
            else:
                seen.append({"row_no": r.row_no, "domain": r.company_url})

        self.save_skipped_results([r["domain"] for r in seen])
        logger.info(
            "Filtered %d records: %d unseen, %d already in history",
            len(records),
            len(unseen),
            len(records) - len(unseen),
        )
        return seen, unseen

    def is_seen(self, record: DomainInput) -> bool:
        return record.company_url.lower() in self.history_domains

    def on_skip(self, record: DomainInput) -> None:
        self._append(self.skip_sheet, record, None, status="skip")

    def on_success(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.good_sheet, record, output)

    def on_error(self, record: DomainInput, output: DomainResponse) -> None:
        self._append(self.error_sheet, record, output, status="error")

    def save_results(self, processed: list[dict], failed: list[dict]) -> None:
        scrape_date_str = datetime.datetime.now().strftime("%m-%d-%Y")

        # 1. HubSpot (Successes only)
        hubspot_payloads = [p["hubspot_payload"] for p in processed if p.get("hubspot_payload")]
        if hubspot_payloads:
            # combined = self.hubspot_client.add_companies(hubspot_payloads)
            for payload in hubspot_payloads:
                if payload["domain"] != "alliedtime.com":
                    continue

                payload["properties"]["apollo_industry_fixed"] = payload["properties"][
                    "apollo_industry"
                ]
                del payload["properties"]["apollo_industry"]
                result = self.hubspot_client.add_company(payload)
                if not result["success"]:
                    logger.error(f"HubSpot batch error: {result['error']}")

            # for err in combined_results.get("errors", []):
            #     logger.error(f"HubSpot batch error: {err}")

        # 2. Good Sheet (Successes)
        if processed:
            rows = [
                self._build_success_row(p["domain"], p.get("data", {}), scrape_date_str)
                for p in processed
            ]
            self.good_sheet.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
            time.sleep(0.5)

        # 3. Error Sheet (Errors)
        if failed:
            rows = [[p["domain"], p.get("error", "Unknown Error"), scrape_date_str] for p in failed]
            self.error_sheet.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
            time.sleep(0.5)

    def save_skipped_results(self, skipped_domains: list[str]):
        scrape_date_str = datetime.datetime.now().strftime("%m-%d-%Y")
        if skipped_domains:
            rows = [[domain, scrape_date_str] for domain in skipped_domains]
            self.skip_sheet.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
            time.sleep(0.5)

    def _build_success_row(self, domain: str, data: dict, scrape_date: str) -> list:
        apollo = data.get("apollo_result", {})
        seamless = data.get("seamless_result", {})
        return [
            domain,
            domain,
            domain,
            data.get("hq_phone_no", ""),
            data.get("website_availability", ""),
            data.get("hq_address_listed", ""),
            data.get("b2c_sales", ""),
            data.get("b2b_sales", ""),
            data.get("industry_classification", ""),
            data.get("ecommerce_platform", ""),
            data.get("lead_status", ""),
            data.get("revenue", ""),
            data.get("shipping_messaging", ""),
            data.get("shipping_methods", ""),
            data.get("carriers", ""),
            data.get("product_size_weight", ""),
            data.get("smallest_product_dim", ""),
            data.get("smallest_product_cubic_size", ""),
            data.get("smallest_product_name", ""),
            data.get("largest_product_dim", ""),
            data.get("largest_product_cubic_size", ""),
            data.get("largest_product_name", ""),
            data.get("redirected_to", ""),
            *apollo.values(),
            *seamless.values(),
            scrape_date,
        ]

    def on_complete(self, records: list[DomainInput], job_id: str = None) -> None:
        # 1. Delete rows from input sheet
        row_numbers = sorted([r.row_no for r in records], reverse=True)
        logger.info(f"Total rows to delete: {len(row_numbers)}")
        for row_no in row_numbers:
            self.input_sheet.delete_rows(row_no)
            time.sleep(0.5)

    def build_response(self, job_id: str) -> dict:
        seen_records, unseen_records = self.get_records()
        CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "5"))
        chunks = []
        for i in range(0, len(unseen_records), CHUNK_SIZE):
            chunk_records = unseen_records[i : i + CHUNK_SIZE]
            chunks.append(
                {
                    "chunk_id": i // CHUNK_SIZE,
                    "domains": [
                        {"domain": r.company_url, "row_no": r.row_no} for r in chunk_records
                    ],
                    "workflow_mode": "production",
                    "job_id": job_id,
                }
            )
        logger.info(
            f"Split {len(unseen_records)} records into {len(chunks)} chunks "
            f"(job_id={job_id}, skipped={len(seen_records)})"
        )
        return {
            "workflow_mode": "production",
            "job_id": job_id,
            "chunks": chunks,
            "skipped_records": seen_records,
        }

    def _append(
        self,
        sheet: gspread.Worksheet,
        domain_input: DomainInput,
        output: Optional[DomainResponse],
        status: Literal["success", "skip", "error"] = "success",
    ) -> None:
        scrape_date_str = datetime.datetime.now().strftime("%m-%d-%Y")

        if status == "success" and output:
            payload = HubSpotDataMapper.map_to_hubspot_payload(domain_input, output)
            result = self.hubspot_client.add_company(payload)
            if not result.get("success"):
                logger.error(f"HubSpot error for {domain_input.company_url}: {result}")
                raise GoogleSheetsError(f"Failed to save to HubSpot: {domain_input.company_url}")

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
                *asdict(output.apollo_result).values(),
                *asdict(output.seamless_result).values(),
                scrape_date_str,
            ]
            sheet.append_row(record, table_range="A1")

        elif status == "error" and output:
            sheet.append_row(
                [domain_input.company_url, output.lead_status, scrape_date_str], table_range="A1"
            )
        elif status == "skip":
            sheet.append_row([domain_input.company_url, scrape_date_str], table_range="A1")
        else:
            logger.error(f"Invalid scrape status or missing output for {domain_input.company_url}")
        time.sleep(0.5)

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


class HubSpotDataMapper:
    """Mapper to convert DomainResponse to HubSpot format."""

    @staticmethod
    def map_to_hubspot_payload(domain_input: DomainInput, output: DomainResponse) -> dict[str, Any]:
        scrape_date = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        scrape_date = scrape_date.replace(hour=0, minute=0, second=0, microsecond=0)

        def get_str(val: Any) -> str:
            return str(val) if val is not None else ""

        return {
            "domain": domain_input.company_url,
            "properties": {
                "name": domain_input.company_url,
                "website": domain_input.company_url,
                "confirmed_website___headquarters_phone__": get_str(output.hq_phone_no),
                "is_website_live_": get_str(output.website_availability),
                "address_listed_on_website_": get_str(output.hq_address_listed),
                "do_the_sell_b2c": get_str(output.b2c_sales).lower(),
                "do_they_sell_b2b": get_str(output.b2b_sales).lower(),
                "industry_type_verified": get_str(output.industry_classification).lower(),
                "ecommerce_platform": get_str(output.ecommerce_platform),
                "scraper_results": get_str(output.lead_status),
                "annual_revenue_scraper": get_str(output.revenue),
                "scraper_shipping_messages": get_str(output.shipping_messaging),
                "scraper_shipping_methods": get_str(output.shipping_methods),
                "scraper_carriers": get_str(output.carriers),
                "scraper_product_size": get_str(output.product_size_weight),
                "hs_redirect_domain": get_str(output.redirected_to),
                "apollo_industry_fixed": get_str(output.apollo_result.industry),
                "hq_phone_number_apollo": get_str(output.apollo_result.company_phone),
                "apollo___of_retail_locations": get_str(output.apollo_result.company_state),
                "apollo_annual_revenue_number_fix_use_this": get_str(
                    output.apollo_result.annual_revenue
                ),
                "employee_count_seamless": get_str(output.seamless_result.num_of_employees),
                "annual_revenue_seamless": get_str(output.seamless_result.annual_revenue),
                "scraper_smallest_product_cubic": get_str(output.smallest_product_cubic_size),
                "scraper_product_dimensions": get_str(output.smallest_product_dim),
                "scraper_smallest_product_name": get_str(output.smallest_product_name),
                "scraper_largest_product_cubic": get_str(output.largest_product_cubic_size),
                "scraper_largest_product_dimensions": get_str(output.largest_product_dim),
                "scraper_largest_product_name": get_str(output.largest_product_name),
                "scrape_date": str(int(scrape_date.timestamp() * 1000)),
            },
        }

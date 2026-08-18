import copy
import datetime
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
from logger import logger


class GoogleSheetsError(Exception):
    """Custom exception for Google Sheets operations."""

    pass


class GoogleSheetsStore:
    """Thin wrapper around gspread: authentication + sheet access.

    NOTE: renamed from GoogleSheetsHandler. This class only knows how to
    talk to Google Sheets - it is NOT the same thing as a RecordStore
    (which knows how to get input records / save results, possibly from
    a completely different backend like HubSpot). Concrete RecordStore
    implementations that use Google Sheets hold a reference to this class.
    """

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


class BaseRecordStore(ABC):
    """Defines the interface that MainExecutor depends on.

    Renamed from BaseSheetStrategy: a "record store" is responsible for
    (1) sourcing input records to process and (2) storing results
    (success/error/skip) wherever they belong - a Google Sheet, HubSpot,
    or in principle anywhere else.
    """

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


class RegularSheetStore(BaseRecordStore):
    """Single-sheet: reads and updates records in place.

    Renamed from RegularSheetStrategy.
    """

    def __init__(self, store: GoogleSheetsStore):
        info = store.spreadsheet_info
        self.sheet = store.open_sheet(info.single_sheet_name, info.single_spreadsheet_id)

    @property
    def pre_enrich(self) -> bool:
        return False

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

            if not GoogleSheetsStore.is_domain(record[0]):
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
            traffic_result = data.get("traffic_result")
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
                data.get("smallest_product_price", ""),
                data.get("largest_product_dim", ""),
                data.get("largest_product_cubic_size", ""),
                data.get("largest_product_name", ""),
                data.get("largest_product_price", ""),
                data.get("redirected_to") or "",
                data.get("old_lead_status") or "",
                traffic_result.get("total_monthly_visits"),
                traffic_result.get("us_traffic"),
                scrape_date_str,
            ]
            for i, val in enumerate(row):
                cells.append(gspread.Cell(item["row_no"], i + 3, val))
        if cells:
            self.sheet.update_cells(cells)
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
            *asdict(output.apollo_result).values(),
            *asdict(output.seamless_result).values(),
            *asdict(output.ahrefs_result).values(),
            datetime.datetime.now().strftime("%m-%d-%Y"),
        ]
        if is_new_record:
            self.sheet.append_row(
                [domain_input.company_name, domain_input.company_url, *row], table_range="A1"
            )
        else:
            self.sheet.update([row], f"C{domain_input.row_no}")
        time.sleep(0.5)


class ProductionSheetStore(BaseRecordStore):
    """Multi-sheet: routes records to good/error/skip sheets and cleans up input.

    Renamed from ProductionSheetStrategy.
    """

    def __init__(self, store: GoogleSheetsStore):
        self.store = store
        info = store.spreadsheet_info
        self.input_sheet = store.open_sheet(info.sheet_name)
        self.good_sheet = store.open_sheet(info.good_results_sheet)
        self.skip_sheet = store.open_sheet(info.skip_results_sheet)
        self.error_sheet = store.open_sheet(info.error_results_sheet)
        self.hubspot_client = HubSpotCompaniesClient(settings.hubspot_api_key)

    @property
    def pre_enrich(self) -> bool:
        return False

    @property
    def check_seen(self) -> bool:
        return True

    def get_records(self) -> list[DomainInput]:
        records = []
        records_to_check = []
        unseen = []
        seen = []
        count = copy.deepcopy(settings.max_input_records)
        unique_input_domains = self.get_unique_records(self.input_sheet.get_all_values("A:B")[1:])
        for index, record in enumerate(unique_input_domains, start=2):
            if count == 0:
                break

            if not record[0]:
                continue

            if not GoogleSheetsStore.is_domain(record[0]):
                seen.append({"row_no": index, "domain": record[0]})
                continue

            records.append(DomainInput(row_no=index, company_url=record[0]))
            records_to_check.append(record[0])
            count -= 1

        history_domains = self._load_history_domains()
        history_domains = history_domains.union(
            self.hubspot_client.get_existing_domains(records_to_check)
        )

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
            for payload in hubspot_payloads:
                result = self.hubspot_client.add_company(payload)
                if not result["success"]:
                    logger.error(f"HubSpot batch error: {result['error']}")

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
        traffic_result = data.get("traffic_result")
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
            data.get("smallest_product_price", ""),
            data.get("largest_product_dim", ""),
            data.get("largest_product_cubic_size", ""),
            data.get("largest_product_name", ""),
            data.get("largest_product_price", ""),
            data.get("redirected_to") or "",
            traffic_result.get("total_monthly_visits"),
            traffic_result.get("us_traffic"),
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
                *asdict(output.ahrefs_result).values(),
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
        info = self.store.spreadsheet_info
        sheet_names = [
            info.good_results_sheet,
            info.skip_results_sheet,
            info.error_results_sheet,
        ]
        domains = set()
        for sheet_name in sheet_names:
            sheet = self.store.open_sheet(sheet_name)
            for record in sheet.get_all_values("A:B")[1:]:
                if record[0] and GoogleSheetsStore.is_domain(record[0]):
                    domains.add(record[0].lower())
        return domains


class HubSpotStore(BaseRecordStore):
    """HubSpot-native source: reads companies needing scrape directly from
    HubSpot (scraper_results == 'requested') and writes results back to
    HubSpot instead of a Google Sheet.

    NOTE: assumes HubSpotCompaniesClient exposes
    `search_companies_by_property(property_name, value, limit)` returning
    a list of `{"id": ..., "properties": {...}}` dicts, and that
    DomainInput has an optional `hubspot_id` field. Neither of those was
    in the files shared so far - adjust names/signatures to match your
    real _types.py / hubspot/records_api.py.
    """

    SCRAPER_RESULTS_PROPERTY = "scraper_results"
    REQUESTED_STATUS = "requested"

    def __init__(self, store: Optional[GoogleSheetsStore] = None):
        # store is optional here and unused for records/results - kept only
        # in case callers want parity (e.g. logging stats via
        # store.update_stats). This strategy's actual source and sink is
        # HubSpot, not a sheet.
        self.store = store
        self.hubspot_client = HubSpotCompaniesClient(settings.hubspot_api_key)

    @property
    def pre_enrich(self) -> bool:
        return False

    @property
    def check_seen(self) -> bool:
        return False

    def get_records(self) -> list[DomainInput]:
        limit = copy.deepcopy(settings.max_input_records)
        companies = self.hubspot_client.search_companies_by_property(
            property_name=self.SCRAPER_RESULTS_PROPERTY,
            value=self.REQUESTED_STATUS,
            limit=limit,
        )

        records = []
        seen_domains = set()
        for company in companies:
            props = company.get("properties", {}) or {}
            domain = props.get("name")
            if not domain or not GoogleSheetsStore.is_domain(domain):
                continue
            domain = domain.lower()
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            records.append(
                DomainInput(
                    row_no=0,
                    company_name=domain,
                    company_url=domain,
                    hubspot_id=company.get("id"),
                )
            )

        logger.info(
            f"Fetched {len(records)} HubSpot records with "
            f"'{self.SCRAPER_RESULTS_PROPERTY}' = '{self.REQUESTED_STATUS}'"
        )
        return records

    def on_success(self, record: DomainInput, output: DomainResponse) -> None:
        self._update_hubspot(record, output)

    def on_error(self, record: DomainInput, output: DomainResponse) -> None:
        self._update_hubspot(record, output)

    def on_complete(self, records: list[DomainInput]) -> None:
        pass  # nothing to clean up on the HubSpot side beyond the property push

    def save_results(self, processed: list[dict], failed: list[dict]) -> None:
        # Results are pushed record-by-record to HubSpot from on_success/
        # on_error, so there's nothing to batch here.
        pass

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
                        {"domain": r.company_url, "hubspot_id": r.hubspot_id} for r in chunk_records
                    ],
                    "workflow_mode": "hubspot",
                    "job_id": job_id,
                }
            )
        logger.info(f"Split {len(records)} records into {len(chunks)} chunks (job_id={job_id})")
        return {"workflow_mode": "hubspot", "job_id": job_id, "chunks": chunks}

    def _update_hubspot(self, domain_input: DomainInput, output: DomainResponse) -> None:
        payload = HubSpotDataMapper.map_to_hubspot_payload(domain_input, output)
        if domain_input.hubspot_id:
            result = self.hubspot_client.update_company(
                domain_input.hubspot_id, payload["properties"]
            )
        else:
            result = self.hubspot_client.add_company(payload)
        if not result.get("success"):
            logger.error(f"HubSpot update failed for {domain_input.company_url}: {result}")
            raise GoogleSheetsError(f"Failed to update HubSpot record: {domain_input.company_url}")


class HubSpotDataMapper:
    """Mapper to convert DomainResponse to HubSpot format."""

    @staticmethod
    def map_to_hubspot_payload(domain_input: DomainInput, output: DomainResponse) -> dict[str, Any]:
        scrape_date = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        scrape_date = scrape_date.replace(hour=0, minute=0, second=0, microsecond=0)

        def get_str(val: Any) -> str:
            return str(val) if val is not None else ""

        lead_status = get_str(output.lead_status)
        is_skip_scrape = lead_status == "skip scrape"

        return {
            "domain": domain_input.company_url,
            "properties": {
                "name": domain_input.company_url,
                "website": domain_input.company_url,
                "confirmed_website___headquarters_phone__": get_str(output.hq_phone_no),
                "is_website_live_": (
                    "No" if is_skip_scrape else get_str(output.website_availability)
                ),
                "address_listed_on_website_": (
                    "No" if is_skip_scrape else get_str(output.hq_address_listed)
                ),
                "do_the_sell_b2c": "no" if is_skip_scrape else get_str(output.b2c_sales).lower(),
                "do_they_sell_b2b": "no" if is_skip_scrape else get_str(output.b2b_sales).lower(),
                "industry_type_verified": get_str(output.industry_classification).lower(),
                "ecommerce_platform": get_str(output.ecommerce_platform),
                "scraper_results": lead_status,
                "hs_lead_status": get_str(output.hs_lead_status),
                "annual_revenue_scraper": get_str(output.revenue),
                "scraper_shipping_messages": get_str(output.shipping_messaging),
                "scraper_shipping_methods": get_str(output.shipping_methods),
                "scraper_carriers": get_str(output.carriers),
                "scraper_product_size": get_str(output.product_size_weight),
                "hs_redirect_domain": get_str(output.redirected_to),
                "scraper_smallest_product_cubic": get_str(output.smallest_product_cubic_size),
                "scraper_product_dimensions": get_str(output.smallest_product_dim),
                "scraper_smallest_product_name": get_str(output.smallest_product_name),
                "scraper_largest_product_cubic": get_str(output.largest_product_cubic_size),
                "scraper_largest_product_dimensions": get_str(output.largest_product_dim),
                "scraper_largest_product_name": get_str(output.largest_product_name),
                "total_monthly_visits": get_str(output.traffic_result.total_monthly_visits),
                "bounce_rate": get_str(output.traffic_result.bounce_rate),
                "pages_per_visit": get_str(output.traffic_result.page_per_visit),
                "time_on_site": get_str(output.traffic_result.time_on_site),
                "traffic_source_search_organic": get_str(output.traffic_result.search_organic),
                "traffic_source_search_paid": get_str(output.traffic_result.search_paid),
                "traffic_source_direct": get_str(output.traffic_result.traffic_source_direct),
                "traffic_source_referrals": get_str(output.traffic_result.traffic_source_referrals),
                "lifecyclestage": get_str(output.lifecycle_stage),
                "usa_traffic": get_str(output.traffic_result.us_traffic),
                "traffic_enrichment_date": scrape_date.strftime("%Y-%m-%d"),
                "traffic_enrichment_status_request": "completed",
                "scrape_date": str(int(scrape_date.timestamp() * 1000)),
            },
        }


def get_store(workflow_mode: str, store: Optional[GoogleSheetsStore] = None) -> BaseRecordStore:
    """Factory: pick the right RecordStore for a given workflow_mode.

    Renamed from get_strategy(). `store` is the GoogleSheetsStore and is
    only required for the sheet-backed modes.
    """
    if workflow_mode == "regular":
        return RegularSheetStore(store)
    elif workflow_mode == "production":
        return ProductionSheetStore(store)
    elif workflow_mode == "hubspot":
        return HubSpotStore(store)
    raise ValueError(f"Unknown workflow_mode: {workflow_mode}")


def create_store(workflow_mode: str) -> BaseRecordStore:
    """Build the RecordStore for a workflow mode without touching Google Sheets.

    In hubspot mode no GoogleSheetsStore is ever constructed - the source
    and sink for that mode are both HubSpot.
    """
    if workflow_mode == "hubspot":
        return HubSpotStore()
    return get_store(workflow_mode, GoogleSheetsStore(settings.spreadsheet_info))

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
    single_spreadsheet_id: str | None = None
    single_sheet_name: str | None = None


@dataclass
class DomainInput:
    row_no: int
    company_url: str
    company_name: str | None = None
    old_lead_status: str | None = None


@dataclass
class ApolloResult:
    company_name: str | None = None
    num_of_employees: str | None = None
    industry: str | None = None
    website: str | None = None
    company_linkedin_url: str | None = None
    company_state: str | None = None
    company_country: str | None = None
    company_postal_code: str | None = None
    company_phone: str | None = None
    annual_revenue: str | None = None
    num_of_retail_locations: str | None = None
    short_description: str | None = None


@dataclass
class SeamlessResult:
    company_name: str | None = None
    website: str | None = None
    industry: str | None = None
    num_of_employees: str | None = None
    revenue_range: str | None = None
    annual_revenue: str | None = None
    company_state_abbr: str | None = None
    company_postal_code: str | None = None
    company_country: str | None = None
    company_linkedin_url: str | None = None
    short_description: str | None = None


@dataclass
class AhrefsResult:
    # domain_rating: float | None = None
    # backlinks: int | None = None
    # organic_traffic: int | None = None
    org_traffic_top_by_country: str | None = None
    top_org_traffic_country_name: str | None = None
    top_org_traffic_country_value: str | None = None
    org_traffic: str | None = None
    paid_traffic: str | None = None
    org_keywords: str | None = None
    paid_keywords: str | None = None
    backlinks: str | None = None
    refdomains: str | None = None


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
    shipping_messaging: str | None = None
    shipping_methods: str | None = None
    carriers: str | None = None
    product_size_weight: str | None = None
    # product_dimensions: str | None = None
    # single_product_dim: str | None = None
    # single_product_cubic_size: str | None = None
    # single_product_name: str | None = None
    smallest_product_dim: str | None = None
    smallest_product_cubic_size: str | None = None
    smallest_product_name: str | None = None
    smallest_product_price: str | None = None
    largest_product_dim: str | None = None
    largest_product_cubic_size: str | None = None
    largest_product_name: str | None = None
    largest_product_price: str | None = None
    apollo_result: ApolloResult | None = None
    seamless_result: SeamlessResult | None = None
    ahrefs_result: AhrefsResult | None = None

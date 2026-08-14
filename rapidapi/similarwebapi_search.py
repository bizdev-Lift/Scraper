import time

import requests

from _types import SimiarWebClientTrafficData
from config import settings
from logger import logger


class RapidSimilarWebClient:
    """API client for fetching domain traffic metrics from SimilarWeb via RapidAPI."""

    BASE_URL = "https://similarweb-traffic-scale-plan.p.rapidapi.com/traffic"
    HOST = "similarweb-traffic-scale-plan.p.rapidapi.com"

    def __init__(self):
        self.headers = {
            "x-rapidapi-key": settings.simiarweb_api_key,
            "x-rapidapi-host": self.HOST,
            "Content-Type": "application/json",
        }

    def _parse_domain_metrics(self, raw_response: dict) -> SimiarWebClientTrafficData:
        """Parse raw API JSON response into a clean metrics dictionary."""
        # Safe extraction of latest monthly visits
        monthly_visits = raw_response.get("EstimatedMonthlyVisits", {})
        latest_visits = monthly_visits.get(max(monthly_visits)) if monthly_visits else None

        engagements = raw_response.get("Engagments", {})
        traffic = raw_response.get("TrafficSources", {})
        us_traffic = 0
        for item in raw_response.get("TopCountryShares", []):
            if item.get("CountryCode") == "US":
                us_traffic = item["Value"]
                break

        return SimiarWebClientTrafficData(
            total_monthly_visits=latest_visits,
            bounce_rate=(
                float(engagements["BounceRate"]) if engagements.get("BounceRate") else None
            ),
            page_per_visit=(
                float(engagements["PagePerVisit"]) if engagements.get("PagePerVisit") else None
            ),
            time_on_site=(
                float(engagements["TimeOnSite"]) if engagements.get("TimeOnSite") else None
            ),
            traffic_source_direct=traffic.get("Direct"),
            traffic_source_referrals=traffic.get("Referrals"),
            search_organic=traffic.get("SearchOrganic"),
            search_paid=traffic.get("SearchPaid"),
            us_traffic=us_traffic,
        )

    def get_domain_traffic(self, domain: str) -> dict:
        """Fetch and parse metrics for a single domain."""
        querystring = {"domain": domain.lower()}
        count = 3
        while count > 0:
            try:
                response = requests.get(
                    self.BASE_URL,
                    headers=self.headers,
                    params=querystring,
                    timeout=10,
                )
                response.raise_for_status()
                return self._parse_domain_metrics(response.json())
            except Exception as err:
                count -= 1
                if count >= 0:
                    logger.error(
                        f"Failed while extracting traffic for domain={domain}. Trying again..."
                    )
                    time.sleep(20)
                    continue

                logger.error(f"Error while processing domain={domain}. Error: {err}")
                return SimiarWebClientTrafficData()

    def get_batch_traffic(self, domains: list[str]) -> dict[str, SimiarWebClientTrafficData]:
        """Fetch and parse metrics for a list of domains."""
        return {domain: self.get_domain_traffic(domain) for domain in domains}

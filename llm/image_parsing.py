import re
import requests
import base64
from urllib.parse import urlparse, urljoin
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
PRODUCT_PATH_HINTS = ("/product", "/products", "/shop", "/item", "/items", "/catalog", "/media/catalog", "/uploads", "/cdn/shop")
# EXCLUDE_HINTS = ("logo", "icon", "sprite", "favicon", "placeholder", "spinner", "loading", "badge", "flag", "payment", "social")

# Generic platform-agnostic hints (for non-Shopify sites)
# GENERIC_PRODUCT_HINTS = ("/product", "/products", "/shop", "/item", "/items", "/catalog", "/uploads")

EXCLUDE_HINTS = (
    "logo", "icon", "favicon", "sprite", "placeholder", "spinner", "loading",
    "badge", "flag", "payment", "social", "facebook", "instagram", "twitter",
    "footer", "header", "banner", "background", "bg-", "/collections/",
    "small_logo", "afghanowned",
)


def extract_product_image_urls(html: str) -> list[str]:
    # Capture src and data-src/data-original (lazy-loaded images)
    pattern = r'<img[^>]+(?:src|data-src|data-original|data-lazy-src)=["\']([^"\']+)["\']'
    candidates = re.findall(pattern, html, re.IGNORECASE)

    results = []
    seen = set()

    for url in candidates:
        url_clean = url.strip()
        if not url_clean or url_clean in seen:
            continue

        # Strip query string for extension/path checks, but keep original for output
        parsed = urlparse(url_clean)
        path_lower = parsed.path.lower()

        # Must end with an image extension
        if not path_lower.endswith(IMAGE_EXTENSIONS):
            continue

        # Must contain a product/shop-related path hint
        if not any(hint in path_lower for hint in PRODUCT_PATH_HINTS):
            continue

        # Exclude common non-product assets
        if any(ex in path_lower for ex in EXCLUDE_HINTS):
            continue

        seen.add(url_clean)
        results.append(url_clean)

    new_urls = dedupe_image_urls(urls=results)
    return new_urls


def dedupe_image_urls(urls: list[str], max_per_group: int = 1) -> list[str]:
    """
    Dedupe image URLs:
    1. Same file at different resolutions -> keep highest resolution.
    2. Files that look like angle variants of the same product (e.g. NameR1-01, NameR1-02, NameR1-03)
       -> keep up to max_per_group from each group.
    """
    # Step 1: group by base filename (strip query string), keep highest width
    by_filename = {}
    for url in urls:
        clean = url.replace("&amp;", "&")
        parsed = urlparse(clean)
        filename = parsed.path.rsplit("/", 1)[-1]  # e.g. EnergydrinkR1-02.jpg

        width_match = re.search(r'width=(\d+)', clean)
        width = int(width_match.group(1)) if width_match else 0

        if filename not in by_filename or width > by_filename[filename][1]:
            by_filename[filename] = (clean, width)

    deduped_by_resolution = [v[0] for v in by_filename.values()]

    # Step 2: group by "base name" with trailing numbers/angle indicators stripped
    # e.g. EnergydrinkR1-01.jpg, EnergydrinkR1-02.jpg, EnergydrinkR1-03.jpg -> "EnergydrinkR1"
    def base_name(filename: str) -> str:
        name = re.sub(r'\.\w+$', '', filename)  # strip extension
        # strip trailing -01, -02, _1, _a, etc.
        name = re.sub(r'[-_]?(\d{1,3}|[a-z])$', '', name, flags=re.IGNORECASE)
        return name.lower()

    groups = {}
    for url in deduped_by_resolution:
        parsed = urlparse(url.replace("&amp;", "&"))
        filename = parsed.path.rsplit("/", 1)[-1]
        key = base_name(filename)
        groups.setdefault(key, []).append(url)

    result = []
    for key, group_urls in groups.items():
        result.extend(group_urls[:max_per_group])

    return result



def download_images(image_urls: list[str], base_url: str, max_images: int = 10, max_size_mb: float = 5.0, max_workers: int = 8) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    def fetch(src: str) -> dict | None:
        full_url = urljoin(base_url, src)
        try:
            resp = requests.get(full_url, headers=headers, timeout=10)
            resp.raise_for_status()

            content = resp.content
            size_mb = len(content) / (1024 * 1024)
            if size_mb > max_size_mb:
                return None

            mime_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            if not mime_type or not mime_type.startswith("image/"):
                guessed, _ = mimetypes.guess_type(full_url)
                mime_type = guessed or "image/jpeg"

            return {
                "url": src,
                "full_url": full_url,
                "data": base64.b64encode(content).decode("utf-8"),
                "mime_type": mime_type,
            }
        except requests.RequestException:
            return None

    urls_to_fetch = image_urls[:max_images]
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(fetch, url): url for url in urls_to_fetch}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            result = future.result()
            if result is not None:
                results[url] = result

    # Preserve original order
    images = [results[url] for url in urls_to_fetch if url in results]
    return images

def create_image_parts(base_url: str, html: str):
    image_urls = extract_product_image_urls(html)
    downloaded_images = download_images(image_urls, base_url=base_url)
    parts = []
    urls = []
    if not downloaded_images:
        return parts

    for image in downloaded_images:
        urls.append(image['url'])
        parts.append({
            "inline_data": {
                "mime_type": image['mime_type'],
                "data": image['data'],
            }
        })

    parts.append({
        "text": '\n'.join(urls)
    })
    return parts

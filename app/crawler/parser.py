import logging
from typing import Optional
from urllib.parse import urljoin, urlparse

import extruct
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger("app.crawler.parser")


def parse(html: str, url: str) -> dict:
    """Returns {'metadata': {...}, 'content': {...}, 'extracted_text': str|None}."""
    soup = BeautifulSoup(html, "lxml")
    structured = _extract_structured_data(html, url)
    metadata = _extract_metadata(soup, url, structured)
    content = _extract_content(soup, html, base_url=url, schema_type=structured.get("schema_type"))
    extracted_text = content.pop("extracted_text", None)
    logger.debug("Parsed: title=%r | word_count=%d | extracted=%d chars",
                 metadata.get("title"), content["word_count"],
                 len(extracted_text) if extracted_text else 0)

    return {"metadata": metadata, "content": content, "extracted_text": extracted_text, "soup": soup}


def _extract_metadata(soup: BeautifulSoup, url: str, structured: dict) -> dict:
    def meta_name(name: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"name": name})
        return tag.get("content", "").strip() or None if tag else None

    def meta_prop(prop: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"property": prop})
        return tag.get("content", "").strip() or None if tag else None

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) or None if title_tag else None

    keywords_raw = meta_name("keywords")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()] if keywords_raw else []

    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    canonical_href = canonical_tag.get("href") if canonical_tag else None
    canonical_url = urljoin(url, canonical_href) if canonical_href else None

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None

    logger.debug("og:title=%r | canonical=%r | keywords=%d",
                 meta_prop("og:title"), canonical_url, len(keywords))

    return {
        "title": title,
        "description": meta_name("description"),
        "keywords": keywords,
        "og_title": meta_prop("og:title"),
        "og_description": meta_prop("og:description"),
        "og_image": meta_prop("og:image"),
        "canonical_url": canonical_url,
        "lang": lang,
        "robots": meta_name("robots"),
        "schema_type": structured.get("schema_type"),
    }


def _extract_content(soup: BeautifulSoup, raw_html: str, base_url: str = "", schema_type: Optional[str] = None) -> dict:
    # Detect product page BEFORE decomposing tags — price itemprop lives in the body,
    # but JSON-LD schema type is passed in from structured data extraction (already done).
    _product_schema_types = {"Product", "IndividualProduct"}
    include_tables = (
        schema_type in _product_schema_types
        or bool(soup.find(attrs={"itemprop": "price"}))
    )

    # Extract links before decomposing — nav/footer <a> tags are still valid links.
    links = _extract_links(soup, base_url)

    # Mutate soup directly — _extract_metadata already returned, nothing else holds a ref.
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    h1 = [t.get_text(strip=True) for t in soup.find_all("h1") if t.get_text(strip=True)]
    h2 = [t.get_text(strip=True) for t in soup.find_all("h2") if t.get_text(strip=True)]
    h3 = [t.get_text(strip=True) for t in soup.find_all("h3") if t.get_text(strip=True)]

    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""

    extracted = trafilatura.extract(
        raw_html,
        include_comments=False,
        include_tables=include_tables,
        no_fallback=False,
    )
    logger.debug("trafilatura: %d chars (include_tables=%s)", len(extracted) if extracted else 0, include_tables)

    # Use trafilatura output for both the preview and word count — it's clean
    # main-content text. Raw body text inflates both with navigation and UI noise.
    # Fall back to raw body text when trafilatura returns nothing.
    content_source = extracted if extracted else body_text

    return {
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "body_text_preview": content_source[:500] if content_source else None,
        "extracted_text": extracted or None,
        "word_count": len(content_source.split()) if content_source else 0,
        "links": links,
    }


# Query parameters that carry no semantic value — tracking, analytics, session IDs.
# Stripped before deduplication so ?utm_source=a and ?utm_source=b resolve to the same URL.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform",
    "fbclid", "fb_action_ids", "fb_action_types",
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "_ga", "_gl", "mc_eid", "msclkid", "twclid",
    "ref_", "pf_rd_p", "pf_rd_r", "pf_rd_s", "pf_rd_t", "pf_rd_i",
    "pd_rd_i", "pd_rd_r", "pd_rd_w", "pd_rd_wg",
    "tag", "ascsubtag",
})

# Path suffixes that indicate low-value utility pages with no crawlable content.
_JUNK_PATH_SUFFIXES = frozenset({
    "/login", "/signin", "/logout", "/signup", "/register",
    "/cart", "/checkout", "/basket", "/bag",
    "/account", "/profile", "/settings", "/preferences",
    "/print", "/download",
})


def _normalise_link(parsed) -> str | None:
    from urllib.parse import parse_qs, urlencode

    # 1. Drop fragment
    parsed = parsed._replace(fragment="")

    # 2. Strip tracking parameters
    if parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=False)
        cleaned = {k: v for k, v in query.items() if k.lower() not in _TRACKING_PARAMS}
        parsed = parsed._replace(query=urlencode(cleaned, doseq=True))

    # 3. Filter junk paths
    path_lower = parsed.path.lower().rstrip("/")
    if any(path_lower == j or path_lower.endswith(j) for j in _JUNK_PATH_SUFFIXES):
        return None

    url = parsed.geturl()
    if len(url) > 300:
        return None

    return url


def _extract_links(soup: BeautifulSoup, base_url: str, max_per_category: int = 50) -> dict:
    """
    Extracts links from an already-parsed soup. Caller must invoke this before
    decomposing structural tags so nav/footer links are included.
    Normalises and deduplicates — tracking-param variants collapse to one URL.
    """
    base_domain = urlparse(base_url).netloc.lower().replace("www.", "")

    internal, external = [], []
    internal_total = 0
    external_total = 0
    seen: set[str] = set()

    _skip_prefixes = ("#", "mailto:", "tel:", "javascript:", "data:")

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or any(href.startswith(p) for p in _skip_prefixes):
            continue

        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)

        if parsed.scheme not in ("http", "https"):
            continue

        normalised = _normalise_link(parsed)
        if normalised is None or normalised in seen:
            continue
        seen.add(normalised)

        link_domain = parsed.netloc.lower().replace("www.", "")
        if link_domain == base_domain:
            internal_total += 1
            if len(internal) < max_per_category:
                internal.append(normalised)
        else:
            external_total += 1
            if len(external) < max_per_category:
                external.append(normalised)

    logger.debug("Links: %d internal, %d external", internal_total, external_total)

    return {
        "internal": internal,
        "external": external,
        "internal_count": internal_total,
        "external_count": external_total,
    }


def _extract_structured_data(html: str, url: str) -> dict:
    try:
        data = extruct.extract(
            html,
            base_url=url,
            syntaxes=["json-ld", "microdata"],
            uniform=True,
        )

        for item in data.get("json-ld", []):
            schema_type = item.get("@type")
            if schema_type:
                if isinstance(schema_type, list):
                    schema_type = schema_type[0]
                logger.debug("Schema type from JSON-LD: %r", schema_type)
                return {"schema_type": schema_type}

        for item in data.get("microdata", []):
            raw_type = item.get("@type", "")
            if raw_type:
                schema_type = raw_type.rstrip("/").split("/")[-1]
                logger.debug("Schema type from microdata: %r", schema_type)
                return {"schema_type": schema_type}

    except Exception as e:
        logger.warning("extruct failed: %s — continuing without structured data", e)

    return {}

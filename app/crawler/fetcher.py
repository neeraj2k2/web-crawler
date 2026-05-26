import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser
from playwright_stealth import Stealth

from app.config import settings

_stealth = Stealth()

logger = logging.getLogger("app.crawler.fetcher")


class UnsupportedContentTypeError(Exception):
    def __init__(self, content_type: str):
        self.content_type = content_type
        super().__init__(f"Expected text/html, got {content_type}")


class HttpError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Remote server returned HTTP {status_code}")


class FetchError(Exception):
    pass


async def fetch(url: str, browser: Browser, client: httpx.AsyncClient) -> tuple[str, str, int]:
    """
    Returns (html, fetcher_used, http_status_code).
    Raises UnsupportedContentTypeError, HttpError, or FetchError.
    """
    logger.info("Fetching URL: %s", url)

    html, status_code = await _try_static(url, client)

    if html is not None:
        logger.info("Static fetch succeeded (%d chars) — running Playwright detection heuristic", len(html))
        if not _needs_playwright(html, url):
            logger.info("Heuristic: static HTML looks complete — using httpx result")
            return html, "httpx", status_code
        logger.info("Heuristic: JS rendering detected — falling back to Playwright")
    else:
        logger.info("Static fetch returned no usable HTML — falling back to Playwright")

    html, status_code = await _fetch_playwright(url, browser)
    return html, "playwright", status_code


async def _try_static(url: str, client: httpx.AsyncClient) -> tuple[Optional[str], Optional[int]]:
    """
    Returns (html, status_code) on success.
    Returns (None, None) to signal Playwright fallback.
    Raises UnsupportedContentTypeError or HttpError for unrecoverable failures.
    Makes up to settings.max_attempts total attempts on transient network errors.
    """
    for attempt in range(1, settings.max_attempts + 1):
        try:
            logger.info("httpx attempt %d/%d: %s", attempt, settings.max_attempts, url)
            response = await client.get(url)

            if response.history:
                logger.info("Redirect chain: %s",
                            " → ".join(str(r.url) for r in response.history) + f" → {response.url}")

            logger.info("httpx response: HTTP %d, Content-Type: %s, final URL: %s",
                        response.status_code,
                        response.headers.get("content-type", "unknown"),
                        response.url)

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                raise UnsupportedContentTypeError(content_type)

            # 5xx: server error — Playwright hits the same broken server, no point retrying.
            if response.is_server_error:
                logger.warning("HTTP %d (server error) — hard failure, skipping Playwright", response.status_code)
                raise HttpError(response.status_code)

            # 4xx: hard fail except 403/429 (bot detection) which Playwright may bypass.
            if response.is_client_error and response.status_code not in (403, 429):
                logger.warning("HTTP %d (client error) — hard failure, skipping Playwright", response.status_code)
                raise HttpError(response.status_code)

            if not response.is_success:
                logger.warning("HTTP %d — possible bot detection, will try Playwright", response.status_code)
                return None, None

            logger.debug("HTML preview (first 500 chars):\n%s", response.text[:500])
            return response.text, response.status_code

        except (UnsupportedContentTypeError, HttpError):
            raise
        except Exception as e:
            if attempt < settings.max_attempts:
                logger.warning("httpx attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                               attempt, settings.max_attempts, type(e).__name__, e, settings.retry_delay)
                await asyncio.sleep(settings.retry_delay)
            else:
                logger.warning("httpx all %d attempts failed (%s: %s) — falling back to Playwright",
                               settings.max_attempts, type(e).__name__, e)
                return None, None

    return None, None


async def _fetch_playwright(url: str, browser: Browser) -> tuple[str, int]:
    """
    Makes up to settings.max_attempts total attempts on transient Playwright failures.
    Each attempt gets a fresh page to avoid stale state.
    """
    last_exc: Exception = FetchError(f"Playwright failed for {url}")

    for attempt in range(1, settings.max_attempts + 1):
        page = await browser.new_page(user_agent=settings.user_agent)
        try:
            await _stealth.apply_stealth_async(page)
            logger.info("Playwright attempt %d/%d: %s (timeout=%ds)",
                        attempt, settings.max_attempts, url, settings.playwright_timeout)
            response = await page.goto(
                url,
                timeout=settings.playwright_timeout * 1000,
                wait_until="domcontentloaded",
            )
            status_code = response.status if response else 200
            logger.info("Playwright page status: HTTP %d", status_code)
            await page.wait_for_timeout(2500)
            html = await page.content()
            logger.info("Playwright fetch succeeded (%d chars)", len(html))
            logger.debug("HTML preview (first 500 chars):\n%s", html[:500])
            return html, status_code
        except Exception as e:
            last_exc = e
            if attempt < settings.max_attempts:
                logger.warning("Playwright attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                               attempt, settings.max_attempts, type(e).__name__, e, settings.retry_delay)
                await asyncio.sleep(settings.retry_delay)
            else:
                logger.warning("Playwright all %d attempts failed", settings.max_attempts)
        finally:
            await page.close()
            logger.info("Playwright page closed (attempt %d)", attempt)

    raise FetchError(f"All fetch strategies failed for {url}: {last_exc}") from last_exc


def _needs_playwright(html: str, url: str) -> bool:
    """
    Returns True if the static HTML looks like a JS shell that needs rendering.
    Any single signal triggers fallback — false positives are safe (just slower).
    """
    soup = BeautifulSoup(html, "lxml")

    # 1. Visible text too short to be real content
    body = soup.find("body")
    visible_text = body.get_text(strip=True) if body else ""
    if len(visible_text) < settings.js_detection_text_threshold:
        logger.info("Heuristic trigger: visible text too short (%d chars < threshold %d)",
                    len(visible_text), settings.js_detection_text_threshold)
        return True

    # 2. SPA framework fingerprints present before JS executes
    if soup.find("div", id="root"):
        logger.info("Heuristic trigger: <div id='root'> found (React)")
        return True
    if soup.find("div", id="app"):
        logger.info("Heuristic trigger: <div id='app'> found (Vue/SPA)")
        return True
    if soup.find(attrs={"data-reactroot": True}):
        logger.info("Heuristic trigger: data-reactroot attribute found")
        return True
    if "__NEXT_DATA__" in html:
        logger.info("Heuristic trigger: __NEXT_DATA__ found (Next.js)")
        return True
    if "ng-version" in html:
        logger.info("Heuristic trigger: ng-version found (Angular)")
        return True
    if "window.__nuxt__" in html:
        logger.info("Heuristic trigger: window.__nuxt__ found (Nuxt.js)")
        return True

    # 3. <noscript> with substantial content — only meaningful if visible text is also short
    noscript = soup.find("noscript")
    if noscript and len(noscript.get_text(strip=True)) > 50 and len(visible_text) < settings.js_detection_text_threshold:
        logger.info("Heuristic trigger: <noscript> has content (%d chars) and visible text is short (%d chars)",
                    len(noscript.get_text(strip=True)), len(visible_text))
        return True

    # 4. Explicit JS requirement messages
    html_lower = html.lower()
    for phrase in ["enable javascript", "javascript is required", "javascript must be enabled"]:
        if phrase in html_lower:
            logger.info("Heuristic trigger: JS requirement string found ('%s')", phrase)
            return True

    # 5. Title missing or matches bare domain name (content hasn't loaded)
    title_tag = soup.find("title")
    if not title_tag or not title_tag.get_text(strip=True):
        logger.info("Heuristic trigger: <title> is missing or empty")
        return True

    hostname = urlparse(url).hostname or ""
    domain = hostname.replace("www.", "")
    title_text = title_tag.get_text(strip=True).lower()
    if title_text in (domain, hostname):
        logger.info("Heuristic trigger: title matches bare domain ('%s')", title_text)
        return True

    logger.info("Heuristic: no JS signals detected — static HTML is usable")
    return False

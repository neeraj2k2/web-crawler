import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.api.models import CrawlRequest, CrawlResponse
from app.config import settings
from app.crawler import classifier, fetcher, parser, robots
from app.crawler.fetcher import FetchError, HttpError, UnsupportedContentTypeError
from app.crawler.models import Classification, Content, Links, Metadata
from app.crawler.utils import resolve_redirect

logger = logging.getLogger("app.api.routes")

router = APIRouter()


@router.post("/crawl", response_model=CrawlResponse)
async def crawl(request: Request, body: CrawlRequest):
    url = str(body.url)
    timestamp = datetime.now(timezone.utc)
    logger.info("Crawl request: %s", url)

    http_client = request.app.state.http_client
    http_client.cookies.clear()

    resolved_url, redirect_status = await resolve_redirect(url, http_client)
    if resolved_url != url:
        logger.info("Redirected to: %s", resolved_url)

    robots_checked = body.respect_robots_txt
    robots_allowed: bool | None = None

    if body.respect_robots_txt:
        robots_allowed = await robots.is_allowed(resolved_url, http_client)
        if not robots_allowed:
            logger.warning("Blocked by robots.txt: %s", resolved_url)
            return CrawlResponse(
                url=url,
                status="blocked",
                crawl_timestamp=timestamp,
                robots_txt_checked=True,
                robots_txt_allowed=False,
                error_code="robots_txt_disallowed",
                message="Crawling disallowed by robots.txt for this path",
            )

    try:
        html, fetcher_used, http_status_code = await fetcher.fetch(
            resolved_url,
            browser=request.app.state.browser,
            client=http_client,
            semaphore=request.app.state.playwright_semaphore,
        )
        logger.info("Fetched via %s | HTTP %d | %d chars", fetcher_used, http_status_code, len(html))
    except UnsupportedContentTypeError as e:
        logger.error("Unsupported content type — %s", e)
        return CrawlResponse(
            url=url, status="error", crawl_timestamp=timestamp,
            robots_txt_checked=robots_checked, robots_txt_allowed=robots_allowed,
            error_code="unsupported_content_type", message=str(e),
        )
    except HttpError as e:
        logger.error("HTTP error — %s", e)
        return CrawlResponse(
            url=url, status="error", crawl_timestamp=timestamp,
            robots_txt_checked=robots_checked, robots_txt_allowed=robots_allowed,
            error_code="http_error", message=str(e), http_status_code=e.status_code,
        )
    except FetchError as e:
        error_msg = str(e).lower()
        error_code = "timeout" if "timeout" in error_msg else "fetch_error"
        logger.error("Fetch failed (%s) — %s", error_code, e)
        return CrawlResponse(
            url=url, status="error", crawl_timestamp=timestamp,
            robots_txt_checked=robots_checked, robots_txt_allowed=robots_allowed,
            error_code=error_code, message=str(e),
            http_status_code=redirect_status,
        )

    parsed = parser.parse(html, resolved_url)

    schema_type = parsed["metadata"].get("schema_type")
    page_type = classifier.classify_page_type(resolved_url, schema_type, parsed["soup"])

    try:
        loop = asyncio.get_running_loop()
        topics = await loop.run_in_executor(
            request.app.state.executor,
            lambda: classifier.extract_topics(
                model=request.app.state.keybert,
                metadata=parsed["metadata"],
                content=parsed["content"],
                extracted_text=parsed["extracted_text"],
                top_k=settings.topics_top_k,
                min_score=settings.min_topic_score,
            ),
        )
    except Exception as e:
        logger.error("Topic extraction failed (%s: %s)", type(e).__name__, e)
        topics = []

    logger.info("Done — page_type=%s | topics=%s | title=%r",
                page_type, topics, parsed["metadata"].get("title"))

    return CrawlResponse(
        url=url,
        status="success",
        crawl_timestamp=timestamp,
        fetcher_used=fetcher_used,
        http_status_code=http_status_code,
        robots_txt_checked=robots_checked,
        robots_txt_allowed=robots_allowed if robots_checked else None,
        metadata=Metadata(**parsed["metadata"]),
        content=Content(
            **{k: v for k, v in parsed["content"].items() if k != "links"},
            links=Links(**parsed["content"]["links"]) if parsed["content"].get("links") else None,
        ),
        classification=Classification(page_type=page_type, topics=topics),
    )


@router.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "http_client_ready": getattr(request.app.state, "http_client", None) is not None,
        "model_loaded": getattr(request.app.state, "keybert", None) is not None,
        "browser_ready": getattr(request.app.state, "browser", None) is not None,
    }

import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

# Prevent HuggingFace tokenizers from spawning parallel worker processes.
# Without this, joblib semaphores are left open when uvicorn reloads/stops,
# producing "leaked semaphore objects" warnings at shutdown.
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import httpx
from fastapi import FastAPI
from keybert import KeyBERT
from playwright.async_api import async_playwright

from app.api.routes import router
from app.config import settings

logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# Keep third-party loggers quieter so our output isn't buried
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=3.0) as _c:
            _ip = (await _c.get("https://api.ipify.org")).text.strip()
        logger.info("Outbound IP: %s (datacenter IPs are blocked by Akamai/Cloudflare)", _ip)
    except Exception:
        logger.info("Outbound IP: could not determine")

    async def _log_request(request: httpx.Request) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug("── Outgoing Request ──────────────────────────")
        logger.debug("  %s %s", request.method, request.url)
        for k, v in request.headers.items():
            logger.debug("    %s: %s", k, v)
        logger.debug("─────────────────────────────────────────────")

    async def _log_response(response: httpx.Response) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug("── Incoming Response ─────────────────────────")
        logger.debug("  Status : %s | URL : %s", response.status_code, response.url)
        for k, v in response.headers.items():
            logger.debug("    %s: %s", k, v)
        logger.debug("─────────────────────────────────────────────")

    logger.info("Creating shared httpx client")
    app.state.http_client = httpx.AsyncClient(
        timeout=settings.static_timeout,
        follow_redirects=True,
        http2=False,
        cookies=None,  # disable cookie jar — Akamai's _abck cookie carries a
                       # JS-challenge-pending flag (~-1~) that we can never resolve;
                       # carrying it on subsequent requests signals we're not a browser
        event_hooks={"request": [_log_request], "response": [_log_response]},
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Priority": "u=0, i",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
    )
    logger.info("httpx client ready (connection pooling + TLS session reuse enabled)")

    logger.info("Loading KeyBERT model: %s", settings.keybert_model)
    app.state.keybert = KeyBERT(model=settings.keybert_model)
    logger.info("KeyBERT model loaded")

    logger.info("Creating thread pool executor (max_workers=%d)", settings.executor_max_workers)
    app.state.executor = ThreadPoolExecutor(max_workers=settings.executor_max_workers)
    logger.info("Thread pool ready")

    logger.info("Launching Playwright Chromium browser")
    playwright = await async_playwright().start()
    app.state.browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    app.state.playwright = playwright
    app.state.playwright_semaphore = asyncio.Semaphore(settings.playwright_max_concurrent)
    logger.info("Playwright browser ready (max_concurrent=%d)", settings.playwright_max_concurrent)

    yield

    logger.info("Shutting down")
    app.state.executor.shutdown(wait=False)
    await app.state.http_client.aclose()
    await app.state.browser.close()
    await app.state.playwright.stop()


app = FastAPI(title="Web Crawler API", version="1.0.0", lifespan=lifespan)
app.include_router(router)

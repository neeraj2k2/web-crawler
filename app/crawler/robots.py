import logging
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger("app.crawler.robots")


async def is_allowed(url: str, client: httpx.AsyncClient) -> bool:
    """
    Returns True if the URL is crawlable per the domain's robots.txt.
    Checks against the '*' wildcard — the rule that applies to all crawlers
    not explicitly named in robots.txt, which is what we are.
    Fails open: any fetch/parse error is treated as allowed.
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    logger.info("Checking robots.txt → %s", robots_url)

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)

    try:
        response = await client.get(robots_url, timeout=5.0)
        if response.status_code == 200:
            rp.parse(response.text.splitlines())
            logger.info("robots.txt fetched and parsed (%d bytes)", len(response.text))
        else:
            logger.info("robots.txt returned HTTP %d — treating as open", response.status_code)
            return True
    except Exception as e:
        logger.info("robots.txt unreachable for %s (%s: %s) — proceeding as allowed (fail-open convention)",
                    url, type(e).__name__, e)
        return True

    allowed = rp.can_fetch("*", url)
    logger.info("robots.txt decision for %s → %s", url, "ALLOWED" if allowed else "DISALLOWED")
    return allowed

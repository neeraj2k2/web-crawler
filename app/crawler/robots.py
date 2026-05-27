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
    logger.debug("Checking robots.txt: %s", robots_url)

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)

    try:
        client.cookies.clear()
        response = await client.get(robots_url, timeout=5.0)
        if response.status_code == 200:
            rp.parse(response.text.splitlines())
        else:
            logger.debug("robots.txt HTTP %d — treating as open", response.status_code)
            return True
    except Exception as e:
        logger.debug("robots.txt unreachable (%s: %s) — fail-open", type(e).__name__, e)
        return True

    allowed = rp.can_fetch("*", url)
    logger.debug("robots.txt: %s", "allowed" if allowed else "disallowed")
    return allowed

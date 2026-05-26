import logging

import httpx

logger = logging.getLogger("app.crawler.utils")


async def resolve_redirect(url: str, client: httpx.AsyncClient) -> tuple[str, int | None]:
    """
    Returns (final_url, initial_http_status).

    Does a no-follow request first to capture the initial status code (e.g. 301),
    then attempts to follow the full chain. If following fails, the initial status
    is still available so callers can surface it in error responses.

    Falls back to (original_url, None) on any error.
    """
    try:
        # Step 1: no-follow to capture the initial status code
        initial = await client.get(url, follow_redirects=False, timeout=5.0)
        initial_status = initial.status_code

        if not initial.is_redirect:
            # No redirect — final URL is the same
            logger.info("No redirect for %s (HTTP %d)", url, initial_status)
            return str(initial.url), initial_status

        # Step 2: follow the full chain to get the true final URL
        try:
            final = await client.get(url, follow_redirects=True, timeout=5.0)
            final_url = str(final.url)
            if final_url != url:
                logger.info("Redirect chain resolved: %s → %s (initial HTTP %d)",
                            url, final_url, initial_status)
            return final_url, initial_status
        except Exception as follow_exc:
            # Following failed (timeout, HTTP/2 error, etc.) — return the first-hop
            # location from the initial 301 response and preserve the status code.
            location = (
                str(initial.next_request.url)
                if initial.next_request
                else initial.headers.get("location", url)
            )
            logger.warning(
                "Redirect follow failed for %s (%s: %s) — using first-hop location %s",
                url, type(follow_exc).__name__, follow_exc, location,
            )
            return location or url, initial_status

    except Exception as e:
        logger.warning(
            "Redirect resolution failed for %s (%s: %s) — using original URL",
            url, type(e).__name__, e,
        )
        return url, None

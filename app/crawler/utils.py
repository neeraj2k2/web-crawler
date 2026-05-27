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
        client.cookies.clear()
        # Step 1: no-follow to capture the initial status code
        initial = await client.get(url, follow_redirects=False, timeout=5.0)
        initial_status = initial.status_code

        if not initial.is_redirect:
            return str(initial.url), initial_status

        # Step 2: follow the full chain to get the true final URL.
        # Clear again — the no-follow request in step 1 may have set Akamai cookies.
        client.cookies.clear()
        try:
            final = await client.get(url, follow_redirects=True, timeout=5.0)
            final_url = str(final.url)
            if final_url != url:
                logger.debug("Redirected: %s → %s", url, final_url)
            return final_url, initial_status
        except Exception as follow_exc:
            location = (
                str(initial.next_request.url)
                if initial.next_request
                else initial.headers.get("location", url)
            )
            logger.warning("Redirect follow failed (%s: %s) — using first-hop %s",
                           type(follow_exc).__name__, follow_exc, location)
            return location or url, initial_status

    except Exception as e:
        logger.warning(
            "Redirect resolution failed for %s (%s: %s) — using original URL",
            url, type(e).__name__, e,
        )
        return url, None

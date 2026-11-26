"""Official bounded open-science provider reader, no HTML crawling."""
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request

HOSTS = {
    "pmc-oa-opendata.s3.amazonaws.com": "/",
    "www.ebi.ac.uk": "/europepmc/webservices/rest/",
    "api.openalex.org": "/works",
    "api.crossref.org": "/works",
}

RETRYABLE_HTTP_STATUSES = frozenset({0, 429, 500, 502, 503, 504})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        return None


def _read_official_once(url, limit=2500000, *, timeout=15):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in HOSTS or not u.path.startswith(HOSTS[u.hostname]):
        raise ValueError("UNAPPROVED_OPEN_SCIENCE_PROVIDER")
    if limit > (192000000 if u.hostname == "pmc-oa-opendata.s3.amazonaws.com" else 8000000):
        raise ValueError("R15_DOWNLOAD_SIZE_HOLD")
    request = urllib.request.Request(url, headers={"User-Agent": "R031-ResearchEvidence/0.15"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as r:
            if urllib.parse.urlsplit(r.geturl()).hostname not in HOSTS:
                raise ValueError("R15_UNAPPROVED_REDIRECT_HOLD")
            body = r.read(limit + 1)
            if len(body) > limit:
                raise ValueError("R15_BODY_TOO_LARGE")
            return {
                "status": r.status,
                "body": body,
                "sha": hashlib.sha256(body).hexdigest(),
                "type": r.headers.get_content_type(),
                "url": r.geturl(),
            }
    except urllib.error.HTTPError as e:
        return {
            "status": e.code,
            "body": b"",
            "sha": None,
            "type": None,
            "url": url,
            "reason": "OFFICIAL_HTTP_" + str(e.code),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "status": 0,
            "body": b"",
            "sha": None,
            "type": None,
            "url": url,
            "reason": "OFFICIAL_NETWORK_HOLD:" + type(e).__name__,
        }


def read_official(url, limit=2500000, *, max_retries=2, backoff_s=0.8, timeout=15, sleep_fn=time.sleep):
    """Fetch bounded open-science resource with exponential backoff on transient HTTP 0/429/5xx failures."""
    attempts = max(0, int(max_retries)) + 1
    last_reply = {"status": 0, "body": b"", "sha": None, "type": None, "url": url, "reason": "RETRY_EXHAUSTED"}
    for attempt in range(attempts):
        reply = _read_official_once(url, limit=limit, timeout=timeout)
        last_reply = reply
        if reply.get("status") not in RETRYABLE_HTTP_STATUSES:
            return reply
        if attempt + 1 < attempts and backoff_s > 0:
            sleep_fn(backoff_s * (2 ** attempt))
    return last_reply


def read_with_retry(reader, url, *, limit=8000000, max_retries=2, backoff_s=0.8, sleep_fn=time.sleep):
    """Call an official reader with bounded retry/backoff, without double-retrying read_official."""
    if reader is read_official:
        return read_official(url, limit=limit, max_retries=max_retries, backoff_s=backoff_s, sleep_fn=sleep_fn)
    attempts = max(0, int(max_retries)) + 1
    last_reply = {"status": 0, "body": b"", "sha": None, "type": None, "url": url, "reason": "RETRY_EXHAUSTED"}
    for attempt in range(attempts):
        reply = reader(url, limit=limit)
        last_reply = reply
        if reply.get("status") not in RETRYABLE_HTTP_STATUSES:
            return reply
        if attempt + 1 < attempts and backoff_s > 0:
            sleep_fn(backoff_s * (2 ** attempt))
    return last_reply

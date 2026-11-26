"""Bounded anonymous public requests; raw bytes and failure receipts retained."""
import hashlib
import ipaddress
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests

HOSTS = {"api.openalex.org", "api.crossref.org", "www.ebi.ac.uk", "europepmc.org",
         "pmc-oa-opendata.s3.amazonaws.com", "eutils.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov",
         "pmc.ncbi.nlm.nih.gov", "api.github.com", "raw.githubusercontent.com",
         "help.openalex.org", "huggingface.co"}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def asset(path):
    from .ir import AssetSHA
    p = Path(path)
    return AssetSHA(sha(p.read_bytes()), str(p), p.stat().st_size)

class Transport:
    def __init__(self, root, max_bytes=32*1024*1024, interval=1.1):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.interval, self.last = max_bytes, interval, 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "R031-R15-source-adapter-benchmark/0.1 (bounded academic research; no credentials)"})

    @staticmethod
    def validate(url):
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443):
            raise ValueError("URL_ALLOWLIST_HOLD")

    @staticmethod
    def validate_format(data, expected):
        if expected == 'pdf' and not data.startswith(b'%PDF-'):
            raise ValueError('PDF_MAGIC_HOLD')
        if expected == 'json': json.loads(data)
        if expected == 'xml':
            from lxml import etree
            etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True))

    def get(self, url, expected=None):
        self.validate(url)
        key = sha(url.encode())
        out = self.root / (key + ".body")
        rec = self.root / (key + ".json")
        if rec.exists():
            old = json.loads(rec.read_text())
            if old.get("state") == "FETCHED" and out.exists() and sha(out.read_bytes()) == old["sha256"]:
                try:self.validate_format(out.read_bytes(),expected)
                except Exception as exc:
                    return out,dict(old,state='FORMAT_HOLD',reason=str(exc))
                return out, old
        receipt = {"url":url, "at_utc":datetime.now(timezone.utc).isoformat(), "state":"ACCESS_HOLD", "attempts":[]}
        data = b""
        try:
            for attempt in range(3):
                time.sleep(max(0, self.interval - (time.monotonic()-self.last)))
                current = url
                for redirect in range(5):
                    self.validate(current)
                    r = self.session.get(current, timeout=(10,45), allow_redirects=False, stream=True)
                    self.last = time.monotonic()
                    receipt["attempts"].append({"status":r.status_code,"url":current})
                    if r.is_redirect:
                        from urllib.parse import urljoin
                        current = urljoin(current, r.headers["Location"])
                        r.close()
                        continue
                    break
                else:
                    raise ValueError("REDIRECT_LIMIT_HOLD")
                if r.status_code in (429,500,502,503,504) and attempt < 2:
                    delay = r.headers.get("Retry-After", str(2**attempt))
                    r.close()
                    try: delay = float(delay)
                    except ValueError: raise ValueError("RETRY_AFTER_DATE_HOLD")
                    if delay > 30: raise ValueError("RATE_LIMIT_LONG_RETRY_HOLD")
                    time.sleep(max(1,delay))
                    continue
                receipt.update(status_code=r.status_code, final_url=current,
                               content_type=r.headers.get("Content-Type"),etag=r.headers.get("ETag"),
                               last_modified=r.headers.get("Last-Modified"))
                for chunk in r.iter_content(65536):
                    if len(data)+len(chunk)>self.max_bytes:
                        r.close()
                        raise ValueError("SIZE_LIMIT_HOLD")
                    data += chunk
                r.close()
                out.write_bytes(data)
                receipt.update(sha256=sha(data), bytes=len(data), path=str(out))
                r.raise_for_status()
                self.validate_format(data,expected)
                receipt["state"] = "FETCHED"
                break
        except Exception as exc:
            receipt["reason"] = f"{type(exc).__name__}: {exc}"
        rec.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        return out if out.exists() else None, receipt

"""Shared HTTP download with retry/backoff.

Both source fetches (NDFD GRIB, Census reference files) hit servers that flake
transiently on the corporate network — Census TLS handshakes drop, tgftp stalls.
One hiccup shouldn't sink a run, so every download retries with linear backoff.
Keeping the retry policy and the NWS-requested User-Agent in one place means the
two callers can't drift apart.
"""
from __future__ import annotations

from typing import Callable

import sys
import time

import requests

# NWS/Census ask automated clients to identify themselves with a contact address.
USER_AGENT = "cbs-news-heat-map/1.0 (data journalism; johnl.kelly@cbsnews.com)"
HEADERS = {"User-Agent": USER_AGENT}


def get_with_retry(url: str, tries: int = 4, timeout: int = 180,
                   validate: Callable[[bytes], None] | None = None) -> bytes:
    """GET ``url`` and return the raw body, retrying transient failures.

    Backoff is linear (2s, 4s, 6s…). Raises the last exception if every attempt
    fails. Pass ``validate`` to also retry on a bad-but-200 payload: it runs
    inside the retry loop and should raise if the body is unusable (e.g. an HTML
    error page where a binary file was expected), so a transient error page
    doesn't get accepted as the final answer.
    """
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            if validate is not None:
                validate(r.content)
            return r.content
        except Exception as e:  # noqa: BLE001 — retry on anything transient
            print(f"  try {attempt}/{tries} failed: {e}", file=sys.stderr)
            if attempt == tries:
                raise
            time.sleep(2 * attempt)

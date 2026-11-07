"""Get a PR's unified diff: from GitHub (cached as raw bytes under pins/) or from a local file.

The pins are the only cache in the system. They exist so the three required runs are
byte-reproducible by whoever opens the repo, and so a network failure during review is a non-event.
"""

import re
import urllib.error
import urllib.request
from pathlib import Path

from . import config

FETCH_TIMEOUT_S = 30
USER_AGENT = "django-review-agent/0.1 (+https://github.com; stdlib urllib)"


class FetchError(RuntimeError):
    """The diff could not be obtained from the given source."""


def pr_number(url: str) -> int | None:
    m = re.match(config.GITHUB_PR_RE, url.strip())
    return int(m.group(3)) if m else None


def fetch(source: str, pins_dir: str = config.PINS_DIR) -> tuple[str, str]:
    """Return (diff_text, label) for a GitHub PR URL or a local diff path.

    label is `pr-<n>` for a PR (also the pin's file stem) or the file stem for a local path.
    Local files are read as-is and never cached: pins are for pinned PRs only.
    """
    path = Path(source)
    if path.is_file():
        return _decode(path.read_bytes()), path.stem

    n = pr_number(source)
    if n is None:
        raise FetchError(f"not a GitHub PR URL or an existing file: {source!r}")

    label = f"pr-{n}"
    pin = Path(pins_dir) / f"{label}.diff"
    if pin.is_file():
        return _decode(pin.read_bytes()), label

    raw = _download(source.rstrip("/").removesuffix(".diff") + ".diff")
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_bytes(raw)  # raw bytes: .gitattributes marks *.diff -text so they stay identical
    return _decode(raw), label


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"GitHub returned HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(f"could not reach {url}: {e}") from e


def _decode(raw: bytes) -> str:
    # GitHub serves UTF-8. Keep line endings exactly as served; the parser handles both.
    return raw.decode("utf-8", errors="replace")

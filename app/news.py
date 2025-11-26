import feedparser
from typing import List, Dict
import datetime
import re
import html as _html


def _strip_html(text: str) -> str:
    if not text:
        return ""
    # Unescape HTML entities then strip tags
    t = _html.unescape(text)
    t = re.sub(r"<[^>]+>", "", t)
    # collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _parse_entry(e) -> Dict:
    # Try to build a normalized published datetime string (local time)
    pub = ""
    try:
        if hasattr(e, 'published_parsed') and e.published_parsed:
            dt = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)
            pub = dt.astimezone().strftime('%Y-%m-%d %H:%M')
        else:
            pub = e.get('published', '') or e.get('updated', '')
    except Exception:
        pub = e.get('published', '') or e.get('updated', '')

    summary = e.get('summary', '') or e.get('description', '') or ''
    return {
        "title": _strip_html(e.get("title", "Sans titre")),
        "link": e.get("link", ""),
        "summary": _strip_html(summary),
        "published": pub,
    }


def fetch_feed(url: str, max_items: int = 6) -> List[Dict]:
    """Fetch a RSS/Atom feed and return a list of simplified entries.

    This is synchronous and small — cache the caller in Streamlit with
    `@st.cache_data` to avoid repeated network calls.
    """
    parsed = feedparser.parse(url)
    items: List[Dict] = []
    if getattr(parsed, 'bozo', False):
        # parsing problem (invalid feed); return empty list
        return items
    for e in parsed.entries[:max_items]:
        items.append(_parse_entry(e))
    return items

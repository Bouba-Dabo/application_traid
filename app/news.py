import feedparser
from typing import List, Dict
import datetime


def _parse_entry(e) -> Dict:
    return {
        "title": e.get("title", "Sans titre"),
        "link": e.get("link", ""),
        "summary": e.get("summary", "") or e.get("description", ""),
        "published": e.get("published", "") or e.get("updated", ""),
    }


def fetch_feed(url: str, max_items: int = 6) -> List[Dict]:
    """Fetch a RSS/Atom feed and return a list of simplified entries.

    This is synchronous and small — cache the caller in Streamlit with
    `@st.cache_data` to avoid repeated network calls.
    """
    parsed = feedparser.parse(url)
    items: List[Dict] = []
    if parsed.bozo:
        # parsing problem (invalid feed); return empty list
        return items
    for e in parsed.entries[:max_items]:
        items.append(_parse_entry(e))
    return items

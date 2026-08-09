"""
Reddit scraper using public JSON API (no auth needed for read-only).
Subreddits: r/Nigeria, r/Africa, r/NigeriaHealth (if exists), r/infectious_diseases,
            r/globalhealth, r/epidemiology.
Uses requests to hit the public .json endpoint — no PRAW/credentials required.
"""

import time
import re
import html
from datetime import datetime, timezone
from typing import List, Dict
import requests

SUBREDDITS = [
    "Nigeria",
    "africa",
    "NigeriaHealth",
    "infectious_diseases",
    "globalhealth",
    "epidemiology",
    "medicine",
    "publichealth",
]

SEARCH_TERMS = ["mpox", "monkeypox", "monkey pox"]

_HEADERS = {
    "User-Agent": "SmartMpox-Scanner/1.0 (public health research; contact: egwuonucheojosamuel@gmail.com)"
}

_STRIP_HTML = re.compile(r'<[^>]+>')


def _clean(text: str) -> str:
    if not text or text == "[deleted]" or text == "[removed]":
        return ""
    text = html.unescape(text)
    text = _STRIP_HTML.sub(" ", text)
    return re.sub(r'\s+', ' ', text).strip()


def _epoch_to_dt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def search_subreddit(subreddit: str, query: str, limit: int = 25) -> List[Dict]:
    """Search a subreddit for a query via public JSON API."""
    url = f"https://old.reddit.com/r/{subreddit}/search.json"
    params = {"q": query, "restrict_sr": 1, "sort": "new", "limit": limit, "t": "year"}
    articles = []
    try:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            # Try Pushshift as fallback for historical data
            return articles
        data = r.json()
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            title   = _clean(d.get("title", ""))
            selftext= _clean(d.get("selftext", ""))[:400]
            url_    = "https://www.reddit.com" + d.get("permalink", "")
            created = d.get("created_utc", 0)
            articles.append({
                "platform":    "reddit",
                "source_name": f"r/{subreddit}",
                "source_url":  url_,
                "published_at": _epoch_to_dt(created) if created else None,
                "title":       title,
                "content_snippet": selftext,
                "full_text":   (title + "\n\n" + selftext),
            })
    except Exception as e:
        print(f"  [WARN] Reddit r/{subreddit} '{query}': {e}")
    return articles


def scrape_reddit_new(subreddit: str, limit: int = 25) -> List[Dict]:
    """Pull newest posts from subreddit (no search term)."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    articles = []
    try:
        r = requests.get(url, headers=_HEADERS, params={"limit": limit}, timeout=15)
        if r.status_code != 200:
            return articles
        data = r.json()
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            title   = _clean(d.get("title", ""))
            selftext= _clean(d.get("selftext", ""))[:400]
            url_    = "https://www.reddit.com" + d.get("permalink", "")
            created = d.get("created_utc", 0)
            articles.append({
                "platform":    "reddit",
                "source_name": f"r/{subreddit}",
                "source_url":  url_,
                "published_at": _epoch_to_dt(created) if created else None,
                "title":       title,
                "content_snippet": selftext,
                "full_text":   (title + "\n\n" + selftext),
            })
    except Exception as e:
        print(f"  [WARN] Reddit r/{subreddit} new: {e}")
    return articles


def scrape_all(delay: float = 1.5) -> List[Dict]:
    """Scrape Reddit for mpox mentions. Returns flat list."""
    all_posts = []

    for sub in SUBREDDITS:
        for term in SEARCH_TERMS:
            print(f"  Reddit r/{sub} — '{term}' ...", end=" ", flush=True)
            posts = search_subreddit(sub, term, limit=25)
            print(f"{len(posts)} posts")
            all_posts.extend(posts)
            time.sleep(delay)

        # Also pull recent posts from Nigeria sub (catch emerging signals)
        if sub in ("Nigeria", "NigeriaHealth"):
            print(f"  Reddit r/{sub} — recent posts ...", end=" ", flush=True)
            posts = scrape_reddit_new(sub, limit=50)
            print(f"{len(posts)} posts")
            all_posts.extend(posts)
            time.sleep(delay)

    # Dedup by URL
    seen = set()
    deduped = []
    for p in all_posts:
        if p["source_url"] not in seen:
            seen.add(p["source_url"])
            deduped.append(p)

    return deduped


if __name__ == "__main__":
    posts = scrape_all()
    print(f"\nTotal Reddit posts: {len(posts)}")
    for p in posts[:5]:
        print(f"  [{p['source_name']}] {p['title'][:80]}")

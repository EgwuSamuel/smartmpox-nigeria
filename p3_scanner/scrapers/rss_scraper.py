"""
RSS scraper for mpox signal detection.
Sources: ProMED, WHO, Google News, 8 Nigerian newspapers, BBC Africa, Reuters.
Uses feedparser (no API keys needed).
"""

import time
import re
import html
from datetime import datetime, timezone
from typing import List, Dict, Optional
import requests
import feedparser

# ── Feed registry ──────────────────────────────────────────────
FEEDS = [
    # ── Global health surveillance wire ──
    {
        "name": "ProMED Mail",
        "url":  "https://promedmail.org/feed/",
        "platform": "promed",
        "priority": "high",
    },
    {
        "name": "WHO Disease Outbreak News",
        "url":  "https://www.who.int/rss-feeds/news-releases-en.xml",
        "platform": "who",
        "priority": "high",
    },
    {
        "name": "HealthMap Mpox",
        "url":  "https://healthmap.org/rss/mpox.rss",
        "platform": "rss_news",
        "priority": "high",
    },
    # ── Google News Nigeria + mpox ──
    {
        "name": "Google News — mpox Nigeria",
        "url":  "https://news.google.com/rss/search?q=mpox+Nigeria&hl=en-NG&gl=NG&ceid=NG:en",
        "platform": "rss_news",
        "priority": "high",
    },
    {
        "name": "Google News — monkeypox Africa",
        "url":  "https://news.google.com/rss/search?q=monkeypox+Africa&hl=en&gl=US&ceid=US:en",
        "platform": "rss_news",
        "priority": "medium",
    },
    # ── Nigerian newspapers ──
    {
        "name": "Premium Times Nigeria",
        "url":  "https://www.premiumtimesng.com/feed",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "Punch Nigeria",
        "url":  "https://punchng.com/feed/",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "Vanguard Nigeria",
        "url":  "https://www.vanguardngr.com/feed/",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "The Guardian Nigeria",
        "url":  "https://guardian.ng/feed/",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "Channels Television",
        "url":  "https://www.channelstv.com/feed/",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "Daily Trust",
        "url":  "https://dailytrust.com/feed",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "The Cable Nigeria",
        "url":  "https://www.thecable.ng/feed",
        "platform": "rss_news",
        "priority": "medium",
    },
    # ── BBC & Reuters Africa ──
    {
        "name": "BBC Africa",
        "url":  "https://feeds.bbci.co.uk/news/world/africa/rss.xml",
        "platform": "rss_news",
        "priority": "medium",
    },
    {
        "name": "Reuters Health",
        "url":  "https://feeds.reuters.com/reuters/healthNews",
        "platform": "rss_news",
        "priority": "medium",
    },
    # ── French sources (cross-border Cameroon/Francophone) ──
    {
        "name": "RFI Afrique",
        "url":  "https://www.rfi.fr/fr/afrique/rss",
        "platform": "rss_news",
        "priority": "low",
    },
    # ── Reddit RSS (no OAuth needed for old.reddit.com) ──
    {
        "name": "r/Nigeria — mpox search",
        "url":  "https://old.reddit.com/r/Nigeria/search.rss?q=mpox&restrict_sr=on&sort=new&t=all",
        "platform": "reddit",
        "priority": "medium",
    },
    {
        "name": "r/africa — mpox search",
        "url":  "https://old.reddit.com/r/africa/search.rss?q=mpox&restrict_sr=on&sort=new&t=all",
        "platform": "reddit",
        "priority": "medium",
    },
    {
        "name": "r/infectious_diseases — mpox search",
        "url":  "https://old.reddit.com/r/infectious_diseases/search.rss?q=mpox&restrict_sr=on&sort=new&t=all",
        "platform": "reddit",
        "priority": "medium",
    },
    {
        "name": "r/epidemiology — mpox search",
        "url":  "https://old.reddit.com/r/epidemiology/search.rss?q=mpox&restrict_sr=on&sort=new&t=all",
        "platform": "reddit",
        "priority": "medium",
    },
    {
        "name": "r/globalhealth — recent",
        "url":  "https://old.reddit.com/r/globalhealth/new.rss?limit=50",
        "platform": "reddit",
        "priority": "low",
    },
    # ── ProMED alternate URL ──
    {
        "name": "ProMED (alt URL)",
        "url":  "https://promedmail.org/rss/",
        "platform": "promed",
        "priority": "high",
    },
    # ── medRxiv mpox preprints ──
    {
        "name": "medRxiv — mpox preprints",
        "url":  "https://connect.medrxiv.org/medrxiv_xml.php?subject=infectious_diseases",
        "platform": "rss_news",
        "priority": "medium",
    },
    # ── Nigerian-language sources (KPI-2: ≥4 languages) ──
    {
        "name": "BBC Hausa",
        "url":  "https://feeds.bbci.co.uk/hausa/rss.xml",
        "platform": "rss_news",
        "priority": "high",
        "forced_lang": "ha",
    },
    {
        "name": "BBC Yoruba",
        "url":  "https://feeds.bbci.co.uk/yoruba/rss.xml",
        "platform": "rss_news",
        "priority": "high",
        "forced_lang": "yo",
    },
    {
        "name": "BBC Pidgin",
        "url":  "https://feeds.bbci.co.uk/pidgin/rss.xml",
        "platform": "rss_news",
        "priority": "high",
        "forced_lang": "pcm",
    },
    # Note: BBC does not have an Igbo RSS feed; Igbo covered via keyword patterns
]

_STRIP_HTML = re.compile(r'<[^>]+>')


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = _STRIP_HTML.sub(" ", text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _parse_date(entry) -> Optional[datetime]:
    """Try to extract a timezone-aware datetime from a feedparser entry."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


_UA = "SmartMpox-Scanner/1.0 (public health research; contact: egwuonucheojosamuel@gmail.com)"


def scrape_feed(feed_cfg: dict, timeout: int = 15) -> List[Dict]:
    """Scrape a single RSS feed. Returns list of article dicts."""
    articles = []
    try:
        # Pre-fetch with requests so we get a real network timeout
        resp = requests.get(feed_cfg["url"], headers={"User-Agent": _UA}, timeout=timeout)
        if resp.status_code != 200:
            return articles
        d = feedparser.parse(resp.content)
        if d.get("bozo") and not d.get("entries"):
            return articles

        for entry in d.get("entries", []):
            title   = _clean(getattr(entry, "title", ""))
            summary = _clean(getattr(entry, "summary", ""))
            content = ""
            if hasattr(entry, "content"):
                for c in entry.content:
                    content += _clean(c.get("value", "")) + " "

            body = (summary + " " + content).strip()[:600]
            url  = getattr(entry, "link", "") or getattr(entry, "id", "")
            pub  = _parse_date(entry)

            articles.append({
                "platform":    feed_cfg["platform"],
                "source_name": feed_cfg["name"],
                "source_url":  url,
                "published_at": pub.isoformat() if pub else None,
                "title":       title,
                "content_snippet": body,
                "full_text":   (title + "\n\n" + body),
                "forced_lang": feed_cfg.get("forced_lang"),
            })

    except Exception as e:
        print(f"  [WARN] Feed {feed_cfg['name']}: {e}")

    return articles


def scrape_all(delay: float = 0.5) -> List[Dict]:
    """Scrape all registered feeds. Returns flat list of article dicts."""
    all_articles = []
    for feed in FEEDS:
        print(f"  Scraping: {feed['name']} ...", end=" ", flush=True)
        articles = scrape_feed(feed)
        print(f"{len(articles)} items")
        all_articles.extend(articles)
        time.sleep(delay)
    return all_articles


if __name__ == "__main__":
    articles = scrape_all()
    print(f"\nTotal articles scraped: {len(articles)}")
    for a in articles[:3]:
        print(f"  [{a['source_name']}] {a['title'][:80]}")

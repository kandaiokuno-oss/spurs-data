#!/usr/bin/env python3
"""
spurs_news.py — スパーズ関連の評価記事をRSSから拾って本文ごと保存する

保存先:
  spurs-data/news/YYYY-MM-DD/<サイト>_<スラッグ>.md
  （先頭にタイトル・URL・公開日時・サイト名、その下に本文テキスト）
  spurs-data/news_state.json  取得済みURLの記録

使い方:
  python3 spurs_news.py              # 新着を取る（spurs_fetch.py の最後からも自動で呼ばれる）
  python3 spurs_news.py --days 3     # 直近3日分まで遡る（初回向け。デフォルトは2日）
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests
import trafilatura

# ===== 設定 =====
OUT_DIR = Path(__file__).resolve().parent / "data"

# (サイト名, RSS URL, スパーズ専門かどうか)
# スパーズ専門でないフィードは、タイトル/概要に Spurs 関連語が含まれる記事だけ保存する
FEEDS = [
    ("poundingtherock", "https://www.poundingtherock.com/rss/current.xml", True),
    ("airalamo", "https://airalamo.com/feed", True),
    ("dailythunder", "https://www.dailythunder.com/feed", False),   # 相手側の視点（OKC戦のときに拾える）
    ("cbssports", "https://www.cbssports.com/rss/headlines/nba/", False),
    ("yahoo", "https://sports.yahoo.com/nba/rss.xml", False),
    ("googlenews", "https://news.google.com/rss/search?q=%22San+Antonio+Spurs%22+OR+Wembanyama+OR+%22Stephon+Castle%22&hl=en-US&gl=US&ceid=US:en", True),
]

KEYWORDS = re.compile(
    r"spurs|wembanyama|wemby|castle|dylan harper|de'aaron fox|mitch johnson|san antonio",
    re.I,
)
PAUSE = 1.5
# ================

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
STATE_FILE = "news_state.json"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] news: {msg}", flush=True)


def load_state():
    p = OUT_DIR / STATE_FILE
    return json.loads(p.read_text()) if p.exists() else {"seen": {}}


def save_state(state):
    (OUT_DIR / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=1))


def slugify(s, n=60):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:n] or "article"


def entry_time(e):
    for k in ("published", "updated"):
        v = e.get(k)
        if v:
            try:
                return parsedate_to_datetime(v).astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                pass
    for k in ("published_parsed", "updated_parsed"):
        v = e.get(k)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_article(url):
    """記事本文をテキストで返す。Google Newsのリダイレクトも追う"""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    r.raise_for_status()
    text = trafilatura.extract(
        r.text, include_comments=False, include_tables=True, favor_recall=True, url=r.url
    )
    return r.url, (text or "").strip()


def run(days=2):
    (OUT_DIR / "news").mkdir(parents=True, exist_ok=True)
    state = load_state()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    saved = 0

    for site, feed_url, spurs_only in FEEDS:
        try:
            fp = feedparser.parse(feed_url, request_headers={"User-Agent": UA})
        except Exception as e:  # noqa: BLE001
            log(f"{site}: フィード取得失敗 {e}")
            continue
        if not fp.entries:
            log(f"{site}: 記事なし（フィードURLが変わったかも）")
            continue

        for e in fp.entries:
            url = e.get("link")
            title = (e.get("title") or "").strip()
            if not url or not title:
                continue
            if url in state["seen"]:
                continue
            t = entry_time(e)
            if t < cutoff:
                continue
            blob = f"{title} {e.get('summary', '')}"
            if not spurs_only and not KEYWORDS.search(blob):
                continue

            try:
                final_url, body = fetch_article(url)
            except Exception as ex:  # noqa: BLE001
                log(f"{site}: 本文取得失敗 {title[:50]} — {ex}")
                state["seen"][url] = {"status": "failed", "title": title}
                continue
            if len(body) < 300:
                log(f"{site}: 本文が短すぎ（有料/ブロック?） {title[:50]}")
                state["seen"][url] = {"status": "short", "title": title}
                continue

            day_dir = OUT_DIR / "news" / t.astimezone().strftime("%Y-%m-%d")
            day_dir.mkdir(exist_ok=True)
            path = day_dir / f"{site}_{slugify(title)}.md"
            path.write_text(
                f"# {title}\n\n"
                f"- site: {site}\n- url: {final_url}\n- published: {t.isoformat()}\n\n---\n\n{body}\n",
                encoding="utf-8",
            )
            state["seen"][url] = {"status": "ok", "title": title, "path": str(path.relative_to(OUT_DIR))}
            saved += 1
            log(f"保存: {path.name}")
            time.sleep(PAUSE)

        save_state(state)

    # 古い既読記録は捨てる（肥大化防止）
    if len(state["seen"]) > 3000:
        state["seen"] = dict(list(state["seen"].items())[-2000:])
        save_state(state)

    log(f"完了: {saved} 記事保存")
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    run(days=args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())

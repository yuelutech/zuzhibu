"""来源等级（rules/cadre.md 第 4 节、第 9 节第 7 条）：事实包来源的 tier 没标时，按链接判定。

1 一级：官网、年报、学校官网（.edu）、本人官网；政府和军队网站（.gov、.mil）；美国附则补充的联邦选举委员会、
  congress-legislators、Bioguide、证券交易委员会 EDGAR；名册登记的机构官网。
2 二级：权威媒体、权威百科、本人署名的公开自述（领英、个人主页）。
3 只作线索：维基百科、维基数据。
判不出的返回 None，按“未标注”处理，不算一级。

只用标准库。
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
ROSTERS = (REPO / "bureaus" / "personnel-sv" / "roster" / "roster.csv",
           REPO / "bureaus" / "personnel-others" / "roster" / "roster.csv")

TIER3_HOSTS = ("wikipedia.org", "wikidata.org", "wikimedia.org")
#: 社交平台、内容平台：域名本身不说明是谁的官网，不按域名算一级。
SOCIAL_HOSTS = ("x.com", "twitter.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "medium.com",
                "substack.com", "github.com", "threads.net")
TIER1_HOSTS = ("bioguide.congress.gov", "congress.gov", "sec.gov", "fec.gov", "theunitedstates.io")
TIER2_HOSTS = (
    "reuters.com", "apnews.com", "bloomberg.com", "nytimes.com", "wsj.com", "ft.com", "washingtonpost.com",
    "politico.com", "axios.com", "cnbc.com", "cnn.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "npr.org",
    "bbc.com", "bbc.co.uk", "theguardian.com", "economist.com", "britannica.com", "news.cn", "xinhuanet.com",
    "people.com.cn", "linkedin.com", "theinformation.com", "techcrunch.com", "theverge.com", "marketscreener.com",
    "forbes.com", "fortune.com", "time.com", "newtimes.co.rw", "ktpress.rw", "rba.co.rw", "taarifa.rw",
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _match(host: str, names) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def _base(host: str) -> str:
    """主域名：nvidianews.nvidia.com → nvidia.com；blog.google → blog.google。"""
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


@lru_cache(maxsize=1)
def official_hosts() -> frozenset[str]:
    """名册登记的机构官网主域名（名册任务只收官方来源），社交平台除外。"""
    hosts = set()
    for path in ROSTERS:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                host = _host(row.get("official_bio_url") or "")
                if host and not _match(host, TIER2_HOSTS + TIER3_HOSTS + SOCIAL_HOSTS):
                    hosts.add(_base(host))
    return frozenset(hosts)


def tier_of(url: str) -> int | None:
    host = _host(url)
    if not host:
        return None
    if _match(host, TIER3_HOSTS):
        return 3
    if "github.com/unitedstates/congress-legislators" in (url or "") or _match(host, TIER1_HOSTS):
        return 1
    if host.endswith((".gov", ".mil", ".edu")) or ".gov." in host or ".edu." in host:
        return 1  # 政府、军队网站和学校官网
    if _match(host, TIER2_HOSTS):
        return 2  # 含领英：本人署名的公开自述
    if _match(host, SOCIAL_HOSTS):
        return None
    if _base(host) in official_hosts():
        return 1
    return None


def tier(source: dict) -> int | None:
    """来源的等级：标了就用标的，没标按链接判定。"""
    value = source.get("tier")
    return value if value in (1, 2, 3) else tier_of(source.get("url") or "")

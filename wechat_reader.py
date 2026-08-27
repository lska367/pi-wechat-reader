#!/usr/bin/env python3
"""Fetch and parse a WeChat Official Account article (mp.weixin.qq.com/s/...).

Emits a JSON document to stdout:
{
  "title": str | null,
  "author": str | null,        # account name
  "pub_time": str | null,      # publish time text as shown on page
  "content": str,              # article text (plain or markdown)
  "images": [str],             # content image URLs (watermark stripped)
  "source_url": str,
  "ok": true
}
On failure: {"ok": false, "error": str, "message": str, "source_url": str}

Strategy: curl_cffi TLS/HTTP2 fingerprint impersonation + WeChat UA.
Verified working 2026-08-27; plain requests/curl hit the "环境异常" wall.
"""

import argparse
import datetime
import json
import re
import sys

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 MicroMessenger/8.0"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}


def strip_watermark(url: str) -> str:
    """Remove WeChat watermark query params but keep the base image."""
    from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

    parts = urlsplit(url)
    if parts.netloc != "mmbiz.qpic.cn":
        return url
    qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
          if k not in {"watermark", "wx_fmt"} or k == "wx_fmt"]
    # drop watermark=1 (often appended as watermark=1&wxtype=png without wx_fmt)
    qs = [(k, v) for k, v in qs if k != "watermark"]
    if not any(k == "wx_fmt" for k, _ in qs):
        qs.append(("wx_fmt", "png"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment))


def fetch(url: str, timeout: int) -> str:
    resp = curl_requests.get(url, impersonate="chrome124", timeout=timeout, headers=HEADERS)
    resp.raise_for_status()
    return resp.text


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("#activity-name") or soup.select_one("h1.rich_media_title")
    title = title_el.get_text(strip=True) if title_el else None

    author_el = soup.select_one("#js_name") or soup.select_one(".rich_media_meta_nickname")
    author = author_el.get_text(strip=True) if author_el else None

    time_el = soup.select_one("#publish_time") or soup.select_one("#js_article_date")
    pub_time = time_el.get_text(strip=True) if time_el else None
    if not pub_time:
        # publish_time is a JS-filled shell in some pages; fall back to the
        # page's `ct = "<unix ts>"` variable, then og/meta tags.
        m = re.search(r'(?:["\']ct["\']?|\bct)\s*[:=]\s*["\']?(\d{10})', html)
        if m:
            pub_time = datetime.datetime.fromtimestamp(int(m.group(1))).strftime("%Y年%m月%d日 %H:%M")
        else:
            meta = (soup.select_one('meta[property="og:published_time"]')
                    or soup.select_one('meta[itemprop="dateUpdate"]'))
            if meta and meta.get("content"):
                pub_time = meta["content"]

    content_el = soup.select_one("#js_content")
    if content_el is None:
        # Anti-bot wall or deleted / migrated article
        body = soup.get_text(" ", strip=True)
        if "环境异常" in body or "完成验证" in body:
            raise RuntimeError("wechat_antispoof", "微信要求环境验证（反爬拦截），请稍后重试或换个网络")
        if "该内容已被发布者删除" in body or "已被发布者删除" in body:
            raise RuntimeError("wechat_deleted", "文章已被发布者删除")
        if "此内容因违规无法查看" in body:
            raise RuntimeError("wechat_blocked", "文章因违规无法查看")
        raise RuntimeError("wechat_no_content", "未能定位正文（页面结构异常）")

    images = []
    for img in content_el.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        clean = strip_watermark(src.split("#")[0])
        if clean not in images:
            images.append(clean)

    return {"title": title, "author": author, "pub_time": pub_time, "images": images, "content_el": content_el}


BLOCK_TAGS = ["p", "section", "h1", "h2", "h3", "h4", "blockquote", "li", "pre"]


def iter_leaf_blocks(content_el):
    """Yield (el, text) for leaf blocks only.

    WeChat nests <p> inside <section> containers; emitting every tagged
    element duplicates text. We emit an element only when it contains no
    block-level child, so containers are covered by their leaves.
    """
    for el in content_el.find_all(BLOCK_TAGS, recursive=True):
        if el.find(BLOCK_TAGS):
            continue  # container; its text comes from descendants
        txt = el.get_text(" ", strip=True)
        if txt:
            yield el, txt


def _is_inside(el, tag):
    return el.find_parent(tag) is not None


def content_to_text(content_el) -> str:
    parts = []
    for el, txt in iter_leaf_blocks(content_el):
        if el.name == "li":
            parts.append(f"- {txt}")
        elif _is_inside(el, "blockquote"):
            parts.append(f"> {txt}")
        else:
            parts.append(txt)
    return "\n\n".join(parts)


def content_to_markdown(content_el, images: list[str]) -> str:
    lines = []
    for el, txt in iter_leaf_blocks(content_el):
        if el.name in ("pre",):
            code = el.get_text("\n", strip=False).strip()
            lines.append(f"```\n{code}\n```")
        elif el.name and el.name.startswith("h") and len(el.name) == 2:
            level = int(el.name[1])
            lines.append(f"{'#' * level} {txt}")
        elif _is_inside(el, "blockquote"):
            lines.append(f"> {txt}")
        elif el.name == "li":
            lines.append(f"- {txt}")
        else:
            lines.append(txt)
    if images:
        lines.append("")
        lines.append("---")
        for img in images:
            lines.append(f"![]({img})")
    return "\n\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--markdown", action="store_true", help="output content as markdown")
    args = ap.parse_args()

    url = args.url.strip()
    if not (url.startswith("https://mp.weixin.qq.com/") or url.startswith("http://mp.weixin.qq.com/")):
        print(json.dumps({"ok": False, "error": "invalid_url",
                          "message": "URL 必须是 mp.weixin.qq.com 的公众号文章链接", "source_url": url}, ensure_ascii=False))
        return

    try:
        html = fetch(url, args.timeout)
        parsed = parse(html)
        if args.markdown:
            content = content_to_markdown(parsed["content_el"], parsed["images"])
        else:
            content = content_to_text(parsed["content_el"])
        del parsed["content_el"]
        parsed["content"] = content
        parsed["source_url"] = url
        parsed["ok"] = True
        print(json.dumps(parsed, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - report everything to the caller
        err = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
        msg = exc.args[1] if len(exc.args) > 1 else str(exc)
        print(json.dumps({"ok": False, "error": err, "message": msg, "source_url": url}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
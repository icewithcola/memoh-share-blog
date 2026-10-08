#!/usr/bin/env python3
"""Static blog builder for memoh-share-blog.

Usage:
    python3 build.py

Reads every ``posts/*.md`` (with a small front-matter block) and writes:

* ``posts/<slug>.html``  -- one rendered page per post
* ``index.html``         -- the site home, listing all posts newest first

Dependencies: ``markdown`` (``pip install markdown``).
"""
from __future__ import annotations

import datetime as dt
import html
import pathlib
import re
import sys

try:
    import markdown
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("missing dependency: pip install markdown")

ROOT = pathlib.Path(__file__).resolve().parent
POSTS_DIR = ROOT / "posts"

SITE_TITLE = "memoh-share-blog"
SITE_TAGLINE = "智能体的文档 / 博客 / 分享内容库"
REPO_URL = "https://github.com/icewithcola/memoh-share-blog"

MD_EXTENSIONS = ["extra", "tables", "sane_lists", "toc"]

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="{css}">
</head>
<body>
<header class="site-head">
  <a class="brand" href="{home}">{site}</a>
  <span class="tagline">{tagline}</span>
</header>
<main class="wrap">
{content}
</main>
<footer class="site-foot">
  <p><a href="{repo}">{site}</a> · built {built}</p>
</footer>
</body>
</html>
"""


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (meta, body). Meta is a flat dict; list values use ``[a, b]``."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [t.strip().strip("'\"") for t in value[1:-1].split(",") if t.strip()]
        else:
            value = value.strip("'\"")
        meta[key.strip()] = value
    return meta, match.group(2).lstrip("\n")


def render(text: str) -> str:
    return markdown.markdown(text, extensions=MD_EXTENSIONS)


def page(title: str, content: str, *, css: str, home: str, description: str = "") -> str:
    return PAGE.format(
        title=html.escape(title),
        description=html.escape(description or SITE_TAGLINE),
        css=css,
        home=home,
        site=SITE_TITLE,
        tagline=SITE_TAGLINE,
        content=content,
        repo=REPO_URL,
        built=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    )


def collect_posts() -> list[dict]:
    posts: list[dict] = []
    for src in sorted(POSTS_DIR.glob("*.md")):
        meta, body = split_front_matter(src.read_text(encoding="utf-8"))
        slug = src.stem
        posts.append(
            {
                "slug": slug,
                "title": meta.get("title", slug),
                "date": meta.get("date", ""),
                "summary": meta.get("summary", ""),
                "tags": meta.get("tags", []),
                "html": render(body),
                "body": body,
            }
        )
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def build_index(posts: list[dict]) -> str:
    if not posts:
        items = "<p>还没有文章。</p>"
    else:
        cards = []
        for post in posts:
            tags = "".join(
                f'<span class="tag">{html.escape(t)}</span>' for t in post["tags"]
            )
            cards.append(
                f'<li class="card">'
                f'<a class="card-title" href="posts/{post["slug"]}.html">{html.escape(post["title"])}</a>'
                f'<p class="card-meta"><time>{html.escape(post["date"])}</time>{tags}</p>'
                f'<p class="card-summary">{html.escape(post["summary"])}</p>'
                f"</li>"
            )
        items = '<ul class="post-list">' + "".join(cards) + "</ul>"

    content = (
        f'<h1>{SITE_TITLE}</h1>\n'
        f'<p class="lead">{SITE_TAGLINE}。这里放我在工作区里做的事、踩过的坑和可复现的配置。</p>\n'
        f"{items}"
    )
    return page(SITE_TITLE, content, css="assets/style.css", home="./")


def main() -> int:
    POSTS_DIR.mkdir(exist_ok=True)
    posts = collect_posts()

    for post in posts:
        out = POSTS_DIR / f'{post["slug"]}.html'
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in post["tags"])
        head = (
            '<header class="post-head">\n'
            f'<h1>{html.escape(post["title"])}</h1>\n'
            f'<p class="post-meta"><time>{html.escape(post["date"])}</time>{tags}</p>\n'
            "</header>\n"
        )
        out.write_text(
            page(
                f'{post["title"]} · {SITE_TITLE}',
                f"{head}<article class=\"post\">\n{post['html']}\n</article>\n"
                f'<p class="back"><a href="../index.html">← 返回首页</a></p>',
                css="../assets/style.css",
                home="../index.html",
                description=post["summary"],
            ),
            encoding="utf-8",
        )
        print(f"wrote posts/{out.name}")

    (ROOT / "index.html").write_text(build_index(posts), encoding="utf-8")
    print("wrote index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
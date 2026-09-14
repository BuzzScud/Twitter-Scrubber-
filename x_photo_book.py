#!/usr/bin/env python3
"""Photo+tweet PDF book for any public X account.

Listing every photo in a date range needs an X API bearer token.
Hydrating known tweet IDs (text + photo) works without a token via fxtwitter.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) x-photo-book/1.0"
PAGE = letter
INK = colors.HexColor("#e7e9ea")
MUTED = colors.HexColor("#71767b")
PANEL = colors.HexColor("#16181c")
BLACK = colors.HexColor("#000000")
LINE = colors.HexColor("#2f3336")
KEEP_C = colors.HexColor("#00ba7c")
DEL_C = colors.HexColor("#f4212e")

MATH_WORDS = re.compile(
    r"\b(formula|equation|theorem|lemma|identity|integral|derivative|"
    r"laplacian|tensor|matrix|vector|proof|diagram|graph of|plot of|"
    r"navier|stokes|fourier|pythagorean|discriminant|inequality)\b",
    re.I,
)
PORTRAIT_WORDS = re.compile(
    r"\b(born|portrait|fields medal|professor|phd|olympiad|photograph)\b",
    re.I,
)
MATH_CHARS = set("∂∇∫∑∏√≡≤≥∈∀∃∞∧∨¬⊂⊃⊗⊕⋅×÷±≈≠∧")

_REPL = {
    "ℵ": "aleph", "⃗": "", "ℏ": "hbar", "μ": "mu", "ν": "nu", "π": "pi",
    "σ": "sigma", "ρ": "rho", "θ": "theta", "φ": "phi", "ϕ": "phi", "ψ": "psi",
    "ω": "omega", "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "eps", "λ": "lambda", "κ": "kappa", "η": "eta", "ξ": "xi", "ζ": "zeta",
    "τ": "tau", "χ": "chi", "∇": "nabla", "∂": "d", "∫": "int", "∑": "sum",
    "∏": "prod", "√": "sqrt", "∞": "inf", "≤": "<=", "≥": ">=", "≠": "!=",
    "≈": "~", "≡": "equiv", "−": "-", "⁄": "/", "∈": "in", "⊂": "subset",
    "∀": "forall", "∃": "exists", "·": ".", "×": "x", "—": "-", "–": "-",
    "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "→": "->", "←": "<-",
    "ℕ": "N", "ℤ": "Z", "ℚ": "Q", "ℝ": "R", "ℂ": "C", "ℓ": "l",
    "¹": "<super>1</super>", "²": "<super>2</super>", "³": "<super>3</super>",
    "⁴": "<super>4</super>", "⁵": "<super>5</super>", "ⁿ": "<super>n</super>",
    "₀": "<sub>0</sub>", "₁": "<sub>1</sub>", "₂": "<sub>2</sub>",
    "₃": "<sub>3</sub>", "₄": "<sub>4</sub>", "ₙ": "<sub>n</sub>",
}


def esc(s: str) -> str:
    for k, v in _REPL.items():
        s = s.replace(k, v)
    s = html.escape(s, quote=False).replace("\n", "<br/>")
    s = s.replace("&lt;super&gt;", "<super>").replace("&lt;/super&gt;", "</super>")
    s = s.replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
    return re.sub(r"[^\x09\x0a\x0d\x20-\x7e\xa0-\xff]", "", s)


def http_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    """Fetch JSON via curl. urllib SSL is broken on some macOS Python builds."""
    cmd = ["curl", "-fsSL", "--retry", "2", "-A", UA, "--max-time", str(timeout)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[:200]
        raise urllib.error.URLError(err or f"curl exit {r.returncode}")
    return json.loads(r.stdout.decode("utf-8"))


def curl_get(url: str, dest: Path, timeout: int = 40) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["curl", "-fsSL", "--retry", "2", "-A", UA, "-o", str(dest), url],
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode == 0 and dest.exists() and dest.stat().st_size > 400:
        return True
    if dest.exists():
        dest.unlink()
    return False


def parse_id(token: str) -> str | None:
    token = token.strip()
    if not token or token.startswith("#"):
        return None
    m = re.search(r"status(?:es)?/(\d+)", token)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{8,25}", token):
        return token
    return None


def load_ids(args) -> list[str]:
    ids: list[str] = []
    if args.ids:
        for part in args.ids.split(","):
            i = parse_id(part)
            if i:
                ids.append(i)
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            i = parse_id(line)
            if i:
                ids.append(i)
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_day(s: str, end: bool = False) -> datetime:
    d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        d = d.replace(hour=23, minute=59, second=59)
    return d


def fmt_date(iso: str) -> str:
    iso = iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M GMT")
    except ValueError:
        return iso


def large_url(url: str) -> str:
    if not url:
        return url
    if "name=orig" in url or "name=large" in url:
        return url
    return url.split("?")[0] + "?name=large"


# --- fetch -----------------------------------------------------------------


def fx_user(handle: str) -> dict:
    data = http_json(f"https://api.fxtwitter.com/{handle.lstrip('@')}")
    user = data.get("user") or {}
    if not user:
        raise SystemExit(f"Could not resolve @{handle} via fxtwitter.")
    return user


def hydrate_fx(tweet_id: str, handle: str = "") -> dict | None:
    h = handle.lstrip("@")
    urls = [
        f"https://api.fxtwitter.com/status/{tweet_id}",
        f"https://api.vxtwitter.com/i/status/{tweet_id}",
    ]
    if h:
        urls = [
            f"https://api.fxtwitter.com/{h}/status/{tweet_id}",
            f"https://api.vxtwitter.com/{h}/status/{tweet_id}",
        ] + urls
    for url in urls:
        try:
            data = http_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            continue
        tweet = data.get("tweet") or data
        if not isinstance(tweet, dict):
            continue
        text = tweet.get("text") or tweet.get("full_text") or ""
        photos: list[str] = []
        media = tweet.get("media") or {}
        if isinstance(media, dict):
            for ph in media.get("photos") or []:
                u = ph.get("url") or ph.get("original_url")
                if u:
                    photos.append(u)
        for u in tweet.get("mediaURLs") or []:
            photos.append(u)
        for item in tweet.get("media_extended") or []:
            if item.get("type") in (None, "image", "photo") and item.get("url"):
                photos.append(item["url"])
        photos = [u for u in photos if u and "video" not in u]
        if not photos and not text:
            continue
        author = tweet.get("author") or {}
        handle = (
            author.get("screen_name")
            or tweet.get("user_screen_name")
            or tweet.get("user_name")
            or ""
        )
        name = author.get("name") or tweet.get("user_name") or handle
        ts = tweet.get("created_timestamp") or tweet.get("date_epoch")
        created = tweet.get("created_at") or tweet.get("date") or ""
        if isinstance(ts, (int, float)):
            created = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M GMT")
        elif isinstance(created, str) and "T" in created:
            created = fmt_date(created)
        return {
            "id": str(tweet.get("id") or tweet_id),
            "handle": handle.lstrip("@"),
            "name": name,
            "date": created,
            "text": text.strip(),
            "photos": photos,
            "url": f"https://x.com/{handle.lstrip('@') or 'i'}/status/{tweet_id}",
        }
    return None


def x_api_user_id(handle: str, bearer: str) -> str:
    url = f"https://api.x.com/2/users/by/username/{urllib.parse.quote(handle.lstrip('@'))}"
    data = http_json(url, headers={"Authorization": f"Bearer {bearer}"})
    uid = (data.get("data") or {}).get("id")
    if not uid:
        raise SystemExit(f"X API could not resolve @{handle}: {data}")
    return uid


def x_api_photo_tweets(
    user_id: str,
    bearer: str,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[str]:
    """Return tweet IDs that have photo attachments, newest first."""
    params = {
        "max_results": "100",
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,attachments",
        "expansions": "attachments.media_keys",
        "media.fields": "type,url",
    }
    if since:
        params["start_time"] = iso_z(since)
    if until:
        params["end_time"] = iso_z(until)
    ids: list[str] = []
    next_token = None
    headers = {"Authorization": f"Bearer {bearer}"}
    while True:
        q = dict(params)
        if next_token:
            q["pagination_token"] = next_token
        url = f"https://api.x.com/2/users/{user_id}/tweets?" + urllib.parse.urlencode(q)
        data = http_json(url, headers=headers)
        media_map = {
            m["media_key"]: m
            for m in (data.get("includes") or {}).get("media") or []
        }
        for tw in data.get("data") or []:
            keys = (tw.get("attachments") or {}).get("media_keys") or []
            if any(media_map.get(k, {}).get("type") == "photo" for k in keys):
                ids.append(tw["id"])
                if limit and len(ids) >= limit:
                    return ids
        next_token = (data.get("meta") or {}).get("next_token")
        if not next_token:
            break
        print(f"  paged… {len(ids)} photo posts so far", flush=True)
    return ids


def classify(text: str) -> tuple[str, str, str]:
    """Caption-only KEEP/DELETE guess. Not a substitute for looking at the photo."""
    math_n = sum(1 for ch in text if ch in MATH_CHARS)
    has_word = bool(MATH_WORDS.search(text))
    portrait = bool(PORTRAIT_WORDS.search(text))
    if has_word or math_n >= 2:
        return "DELETE", "formula_card", "Caption looks like a formula/diagram card."
    if portrait:
        return "KEEP", "portrait", "Caption looks like a person/biography post."
    return "KEEP", "photo", "No strong formula-card signal in the caption."


# --- images / pdf ----------------------------------------------------------


def round_avatar(src: Path, dest: Path, size: int = 160) -> None:
    im = PILImage.open(src).convert("RGBA").resize((size, size), PILImage.LANCZOS)
    mask = PILImage.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    im.putalpha(mask)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)


def download_photo(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 800:
        return True
    return curl_get(large_url(url), dest)


def fitted_image(path: Path, max_w, max_h):
    try:
        img = Image(str(path))
        iw, ih = img.imageWidth, img.imageHeight
        if iw <= 0 or ih <= 0:
            return None
        scale = min(max_w / iw, max_h / ih, 1.0)
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        img.hAlign = "CENTER"
        return img
    except Exception:
        return None


def styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CoverTitle", parent=ss["Title"], fontSize=22, leading=26,
                          alignment=TA_CENTER, textColor=INK, spaceAfter=10))
    ss.add(ParagraphStyle("CoverSub", parent=ss["Normal"], fontSize=10, leading=14,
                          alignment=TA_CENTER, textColor=MUTED, spaceAfter=8))
    ss.add(ParagraphStyle("H", parent=ss["Heading1"], fontSize=16, leading=20,
                          spaceBefore=8, spaceAfter=8, textColor=INK))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.5, leading=13,
                          alignment=TA_JUSTIFY, textColor=INK, spaceAfter=6))
    ss.add(ParagraphStyle("Name", parent=ss["Normal"], fontSize=11, leading=14,
                          fontName="Times-Bold", textColor=INK, spaceAfter=0))
    ss.add(ParagraphStyle("Handle", parent=ss["Normal"], fontSize=9, leading=12,
                          textColor=MUTED, spaceAfter=6))
    ss.add(ParagraphStyle("Tweet", parent=ss["Normal"], fontSize=9.5, leading=13,
                          textColor=INK, spaceAfter=8))
    ss.add(ParagraphStyle("TweetSmall", parent=ss["Normal"], fontSize=8, leading=11,
                          textColor=INK, spaceAfter=6))
    ss.add(ParagraphStyle("RecKeep", parent=ss["Normal"], fontSize=10, leading=13,
                          fontName="Times-Bold", textColor=KEEP_C, spaceAfter=4))
    ss.add(ParagraphStyle("RecDel", parent=ss["Normal"], fontSize=10, leading=13,
                          fontName="Times-Bold", textColor=DEL_C, spaceAfter=4))
    ss.add(ParagraphStyle("Link", parent=ss["Normal"], fontSize=8, leading=11,
                          textColor=colors.HexColor("#1d9bf0"), spaceAfter=6))
    ss.add(ParagraphStyle("Why", parent=ss["Normal"], fontSize=8, leading=11,
                          textColor=MUTED, spaceAfter=2))
    ss.add(ParagraphStyle("Index", parent=ss["Normal"], fontSize=8, leading=11,
                          textColor=MUTED, spaceAfter=4))
    ss.add(ParagraphStyle("Missing", parent=ss["Normal"], fontSize=10, leading=13,
                          alignment=TA_CENTER, textColor=MUTED))
    return ss


def header_footer(handle: str, since: str, until: str):
    label = f"@{handle}  ·  photo book"
    if since or until:
        label += f"  ·  {since or '…'} – {until or '…'}"

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BLACK)
        canvas.rect(0, 0, PAGE[0], PAGE[1], fill=1, stroke=0)
        canvas.setFillColor(MUTED)
        canvas.setFont("Times-Roman", 8)
        canvas.drawString(0.45 * inch, PAGE[1] - 0.32 * inch, label)
        canvas.drawRightString(PAGE[0] - 0.45 * inch, PAGE[1] - 0.32 * inch, "photo + full tweet")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(0.45 * inch, PAGE[1] - 0.42 * inch, PAGE[0] - 0.45 * inch, PAGE[1] - 0.42 * inch)
        canvas.line(0.45 * inch, 0.38 * inch, PAGE[0] - 0.45 * inch, 0.38 * inch)
        canvas.drawCentredString(PAGE[0] / 2, 0.22 * inch, f"page {doc.page}")
        canvas.restoreState()

    return _draw


def post_flowables(ss, post: dict, img_dir: Path, avatar: Path, idx: int):
    page_w = PAGE[0] - 0.9 * inch
    photo_h = 5.15 * inch
    img_path = Path(post["image_path"]) if post.get("image_path") else img_dir / f"{post['id']}.jpg"
    photo = None
    if img_path.exists():
        photo = fitted_image(img_path, page_w - 8, photo_h - 8)
    if photo is None:
        photo = Paragraph("Photo could not be downloaded.", ss["Missing"])
    photo_tbl = Table([[photo]], colWidths=[page_w])
    photo_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLACK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
    ]))
    name = post.get("name") or post.get("handle") or ""
    handle = post.get("handle") or ""
    if avatar.exists():
        av = Image(str(avatar), width=28, height=28)
        name_block = [Paragraph(esc(name), ss["Name"]), Paragraph("@" + esc(handle), ss["Handle"])]
        head = Table([[av, name_block]], colWidths=[36, page_w - 48])
        head.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ]))
    else:
        head = Paragraph(f"<b>{esc(name)}</b>  @{esc(handle)}", ss["Name"])
    cap = post.get("text") or ""
    tweet_style = ss["Tweet"] if len(cap) < 1600 else ss["TweetSmall"]
    rec = post.get("rec") or "KEEP"
    rec_style = ss["RecKeep"] if rec == "KEEP" else ss["RecDel"]
    return [
        photo_tbl,
        Spacer(1, 8),
        head,
        Paragraph(esc(cap), tweet_style),
        Spacer(1, 4),
        Paragraph(esc(post.get("date") or ""), ss["Handle"]),
        Paragraph(f'<link href="{post["url"]}">{post["url"]}</link>', ss["Link"]),
        Paragraph(
            f'{rec}  ·  {esc(post.get("kind") or "photo")}',
            rec_style,
        ),
        Paragraph(esc(post.get("why") or ""), ss["Why"]),
        Paragraph(esc(f"{idx}  ·  {post.get('date') or ''}"), ss["Index"]),
        Spacer(1, 8),
    ]


def write_markdown(posts: list[dict], handle: str, out: Path, since: str, until: str):
    lines = [
        f"# @{handle} photo book",
        "",
        f"Window: {since or '—'} through {until or '—'}",
        f"Posts: {len(posts)}",
        "",
    ]
    for i, p in enumerate(posts, 1):
        lines += [
            f"## {i}. {p.get('date', '')} — **{p.get('rec', 'KEEP')}**",
            "",
            f"- URL: {p['url']}",
            f"- Caption: {p.get('text', '')}",
            f"- Why: {p.get('why', '')}",
            "",
        ]
    path = out / f"{handle}-photo-book.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_pdf(posts: list[dict], handle: str, out: Path, img_dir: Path, avatar: Path,
              since: str, until: str):
    ss = styles()
    story = [
        Spacer(1, 1.6 * inch),
        Paragraph(f"@{esc(handle)}", ss["CoverTitle"]),
        Paragraph("Photo book: full image + full tweet", ss["CoverTitle"]),
        Paragraph(f"{since or 'start'} through {until or 'now'}", ss["CoverSub"]),
        Paragraph(
            f"{len(posts)} photo posts. Photos are name=large and never upscaled. "
            "Long tweets wrap onto the next page instead of clipping.",
            ss["CoverSub"],
        ),
        PageBreak(),
    ]
    for i, p in enumerate(posts, 1):
        story.extend(post_flowables(ss, p, img_dir, avatar, i))
        story.append(PageBreak())
    pdf_path = out / f"{handle}-photo-book.pdf"
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=PAGE,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.5 * inch,
        title=f"@{handle} photo book",
        author="x-photo-book",
    )
    doc.build(story, onFirstPage=header_footer(handle, since, until),
              onLaterPages=header_footer(handle, since, until))
    return pdf_path


# --- main ------------------------------------------------------------------


class BookError(Exception):
    pass


def parse_ids_text(text: str) -> list[str]:
    """Pull tweet IDs out of anything pasted: URLs, bare IDs, or a copied blob of links/HTML."""
    ids: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", text or ""):
        found = re.findall(r"status(?:es)?/(\d+)", token)
        if not found and re.fullmatch(r"\d{8,25}", token):
            found = [token]
        for i in found:
            if i not in seen:
                seen.add(i)
                ids.append(i)
    return ids


TWITTER_EPOCH_MS = 1288834974657


def snowflake_time(tweet_id: str) -> datetime | None:
    """Post time encoded in a tweet ID. IDs from before Nov 2010 carry no time."""
    n = int(tweet_id)
    if n < 10**15:
        return None
    return datetime.fromtimestamp(((n >> 22) + TWITTER_EPOCH_MS) / 1000, tz=timezone.utc)


def collect_posts(args) -> list[dict]:
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        return data["posts"] if isinstance(data, dict) and "posts" in data else data

    ids = load_ids(args)
    extra = parse_ids_text(getattr(args, "ids_text", "") or "")
    for i in extra:
        if i not in ids:
            ids.append(i)
    bearer = args.bearer or os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
    since = parse_day(args.since) if args.since else None
    until = parse_day(args.until, end=True) if args.until else None
    note = getattr(args, "progress", None) or (lambda msg, c=0, t=0: print(msg, flush=True))

    if not ids:
        if not bearer:
            raise BookError(
                "No tweet IDs and no X API token. Paste tweet URLs, or save a bearer token in Settings."
            )
        note(f"Resolving @{args.user} via X API…")
        uid = x_api_user_id(args.user, bearer)
        note(f"Paging photo tweets for user {uid}…")
        ids = x_api_photo_tweets(uid, bearer, since, until, args.limit or 0)
        note(f"Found {len(ids)} photo tweet ids.", 0, len(ids))
    elif since or until:
        # Pasted IDs: the ID itself carries the post time, so Since/Until work without the API.
        kept = []
        for tid in ids:
            t = snowflake_time(tid)
            if t is None or ((not since or t >= since) and (not until or t <= until)):
                kept.append(tid)
        skipped = len(ids) - len(kept)
        if skipped:
            note(f"Skipped {skipped} post{'s' if skipped != 1 else ''} outside the date range.", 0, len(kept))
        ids = kept
        if not ids:
            raise BookError("None of the pasted posts fall between Since and Until.")

    if args.limit:
        ids = ids[: args.limit]

    posts = []
    for i, tid in enumerate(ids, 1):
        note(f"Hydrating {tid}", i, len(ids))
        post = hydrate_fx(tid, args.user)
        if not post:
            note(f"Skip {tid}: could not hydrate", i, len(ids))
            continue
        if not post["photos"]:
            note(f"Skip {tid}: no photo", i, len(ids))
            continue
        if args.classify:
            rec, kind, why = classify(post["text"])
        else:
            rec, kind, why = "KEEP", "photo", "Dump mode (pass --classify to guess formula cards)."
        post["rec"] = rec
        post["kind"] = kind
        post["why"] = why
        post["media"] = post["photos"][0]
        posts.append(post)
    return posts


def build_book(
    *,
    user: str,
    out: Path,
    ids: str = "",
    ids_file: str | None = None,
    ids_text: str = "",
    since: str | None = None,
    until: str | None = None,
    classify_flag: bool = False,
    limit: int = 0,
    bearer: str | None = None,
    from_json: str | None = None,
    progress=None,
) -> dict:
    """Run the full pipeline. Used by the CLI and the web app."""
    user = user.lstrip("@")
    out = Path(out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    img_dir = out / "images"
    img_dir.mkdir(exist_ok=True)
    note = progress or (lambda msg, c=0, t=0: print(msg, flush=True))

    note(f"Looking up @{user}…")
    try:
        profile = fx_user(user)
    except Exception as e:
        note(f"Profile lookup failed ({e}); continuing with handle only.")
        profile = {"screen_name": user, "name": user, "avatar_url": ""}

    avatar_round = out / "avatar.png"
    av_url = (profile.get("avatar_url") or "").replace("_normal", "")
    if av_url:
        raw = out / "avatar_raw.jpg"
        if curl_get(av_url, raw):
            round_avatar(raw, avatar_round)

    args = argparse.Namespace(
        user=user,
        since=since,
        until=until,
        ids=ids or None,
        ids_file=ids_file,
        ids_text=ids_text,
        from_json=from_json,
        bearer=bearer,
        classify=classify_flag,
        limit=limit or 0,
        progress=note,
    )
    posts = collect_posts(args)
    if not posts:
        raise BookError("No photo posts collected.")

    for i, p in enumerate(posts, 1):
        ext = ".png" if p["media"].lower().split("?")[0].endswith(".png") else ".jpg"
        dest = img_dir / f"{p['id']}{ext}"
        note(f"Downloading photo {p['id']}", i, len(posts))
        download_photo(p["media"], dest)
        p["image_path"] = str(dest)

    (out / "posts.json").write_text(
        json.dumps({"user": user, "posts": posts}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    note("Writing markdown…", len(posts), len(posts))
    md = write_markdown(posts, user, out, since or "", until or "")
    note("Writing PDF…", len(posts), len(posts))
    pdf = write_pdf(posts, user, out, img_dir, avatar_round, since or "", until or "")
    note("Done.", len(posts), len(posts))
    return {
        "posts": posts,
        "pdf": pdf,
        "md": md,
        "avatar": avatar_round,
        "out": out,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Photo+tweet PDF book for a public X account")
    ap.add_argument("user", help="X handle without @")
    ap.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", help="YYYY-MM-DD (inclusive calendar day)")
    ap.add_argument("--ids", help="Comma-separated tweet IDs or URLs")
    ap.add_argument("--ids-file", help="Text file of tweet IDs or URLs, one per line")
    ap.add_argument("--from-json", help="Rebuild from a previous posts.json")
    ap.add_argument("--bearer", help="X API v2 bearer token (else $X_BEARER_TOKEN)")
    ap.add_argument("--out", default=".", help="Output directory")
    ap.add_argument("--limit", type=int, default=0, help="Max photo posts")
    ap.add_argument("--classify", action="store_true",
                    help="Caption-only KEEP/DELETE guess for formula cards")
    args = ap.parse_args(argv)
    try:
        result = build_book(
            user=args.user,
            out=args.out,
            ids=args.ids or "",
            ids_file=args.ids_file,
            since=args.since,
            until=args.until,
            classify_flag=args.classify,
            limit=args.limit or 0,
            bearer=args.bearer,
            from_json=args.from_json,
        )
    except BookError as e:
        print(e, file=sys.stderr)
        return 1
    pdf = result["pdf"]
    print(f"markdown  {result['md']}")
    print(f"pdf       {pdf}  ({pdf.stat().st_size} bytes)")
    print(f"posts     {result['out'] / 'posts.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

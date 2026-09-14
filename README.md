# x-photo-book

Build the same **photo-on-top + full tweet underneath** PDF we used for `@mathemetica`, for **any public X account**.

It does **not** log into X as you. It cannot magically open an account’s Media tab. Getting the *list* of posts is the hard part; turning those posts into a sharp PDF is the easy part.

## What it produces

For each photo post:

- the **full image** (`pbs.twimg.com` `name=large`, never upscaled)
- the **full tweet** (display name, `@handle`, caption, date, URL)
- optional KEEP / DELETE guess from the caption (formula-card heuristic)

Outputs, in `--out`:

- `USER-photo-book.pdf`
- `USER-photo-book.md`
- `posts.json`
- `images/` (downloaded photos)
- `avatar.png`

## Can it do any account?

| Step | Works without X API? | Works with X API bearer token? |
|---|---|---|
| Look up profile / avatar | Yes (`api.fxtwitter.com`) | Yes |
| Hydrate one tweet (text + photo URL) | Yes (`api.fxtwitter.com` / `api.vxtwitter.com`) | Yes |
| **List** every photo post in a date range | **With the bookmarklet** — it scrolls the Media tab in your own signed-in browser | **Yes** — user timeline, paged |
| Download photos + write the PDF | Yes | Yes |

So:

- **You have tweet URLs or IDs** → no API key needed.
- **You want “all photos from @user since DATE”** → use the web app’s *Send to x-photo-book* bookmarklet (below), or set `X_BEARER_TOKEN` (X API v2). Free/basic tiers are rate-limited; paid/pro can page further.

## Collecting URLs without an API key (web app)

The New job page has a **Send to x-photo-book** link. Drag it to the Favorites bar once. Then:

1. Open the account’s Media tab on X (or the “search Since–Until” link, which builds `from:user filter:images since:… until:…`).
2. Click the bookmark, then **Auto-scroll**. X only keeps on-screen tweets in the page, so it picks up links at every scroll step and ignores other accounts’ retweets.
3. **Send to app** opens the New job page with the URLs filled in. **Copy** puts them on the clipboard instead.

Since / Until also trim pasted URLs with no API: a tweet ID encodes its post time (`(id >> 22) + 1288834974657` ms). The URL box accepts any pasted text that contains post links. The bookmarklet source is `static/collect.js`. In Safari, if clicking the bookmark does nothing, turn on Develop → Allow JavaScript from Smart Search Field.

## Install

```bash
cd ~/Desktop/x-photo-book
python3 -m pip install -r requirements.txt
```

`curl` must be on your PATH (it is on macOS).

## Usage

### 1. From tweet URLs / IDs (no API key)

```bash
python3 x_photo_book.py mathemetica \
  --ids 2099183045131829512,2099338939131949384 \
  --out ~/Desktop/x-photo-book-out
```

Or a text file, one URL or ID per line:

```
https://x.com/mathemetica/status/2099183045131829512
2099338939131949384
```

```bash
python3 x_photo_book.py mathemetica --ids-file urls.txt --out ~/Desktop/x-photo-book-out
```

### 2. From an account + date range (needs X API)

```bash
export X_BEARER_TOKEN="YOUR_BEARER_TOKEN"
python3 x_photo_book.py mathemetica \
  --since 2026-03-01 \
  --until 2026-09-14 \
  --out ~/Desktop/x-photo-book-out
```

`--until` is exclusive (X API `end_time`). `--limit N` stops after N photo posts (useful for a test).

### 3. Formula-card KEEP / DELETE guess

```bash
python3 x_photo_book.py mathemetica --ids-file urls.txt --classify --out ~/Desktop/x-photo-book-out
```

This is **caption-only**. It is not as good as opening the photo. Default without `--classify`: every post is listed as `KEEP` (dump, don’t guess).

## How listing works with the API

1. Resolve `@user` → user id  
2. Page `GET /2/users/:id/tweets` with `start_time` / `end_time`  
3. Keep tweets that have `type=photo` media  
4. Skip retweets  
5. Hydrate any missing text via fxtwitter if needed  
6. Download `name=large` images  
7. Write markdown + PDF (same layout as the last mathemetica book)

## Limits (same ones we hit by hand)

- X search/API caps how many posts you get per request; the program pages until the window is empty or `--limit` hits.
- Protected accounts, deleted posts, and some old media URLs will fail; those pages say so instead of inventing a post.
- fxtwitter/vxtwitter are unofficial hydrate endpoints. If they go down, use `--bearer` or pass `--from-json` from a previous run.
- `--from-json posts.json` rebuilds the PDF from a saved run without refetching.

## Rebuild a PDF from a previous run

```bash
python3 x_photo_book.py mathemetica --from-json ~/Desktop/x-photo-book-out/posts.json --out ~/Desktop/x-photo-book-out
```

## Web app (server + SQLite)

```bash
cd ~/Desktop/x-photo-book
python3 -m pip install -r requirements.txt
python3 webapp.py
```

Open [http://127.0.0.1:5055](http://127.0.0.1:5055).

- Jobs, posts, KEEP/DELETE flags, and an optional API token live in `data/app.db`
- Each job’s photos and PDF live in `data/jobs/<id>/`
- Paste tweet URLs to run without an API key
- Flip KEEP/DELETE on a job page, then **Rebuild PDF from flags**
- Token in Settings is stored locally in SQLite only

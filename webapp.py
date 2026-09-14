#!/usr/bin/env python3
"""Local web app: SQLite jobs + photo-book PDF for any public X account."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

import x_photo_book as book

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "app.db"
JOBS_DIR = DATA / "jobs"

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.secret_key = "x-photo-book-local"

_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL,
                since TEXT,
                until TEXT,
                ids_text TEXT,
                classify INTEGER NOT NULL DEFAULT 0,
                limit_n INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT,
                progress_current INTEGER DEFAULT 0,
                progress_total INTEGER DEFAULT 0,
                error TEXT,
                post_count INTEGER DEFAULT 0,
                keep_count INTEGER DEFAULT 0,
                delete_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                tweet_id TEXT NOT NULL,
                handle TEXT,
                name TEXT,
                date TEXT,
                text TEXT,
                url TEXT,
                media TEXT,
                rec TEXT,
                kind TEXT,
                why TEXT,
                image_relpath TEXT,
                sort_order INTEGER
            );
            """
        )


def get_setting(key: str, default: str = "") -> str:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def job_dir(job_id: int) -> Path:
    p = JOBS_DIR / str(job_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_job(job_id: int) -> sqlite3.Row | None:
    with db() as con:
        return con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs() -> list[sqlite3.Row]:
    with db() as con:
        return con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()


def list_posts(job_id: int) -> list[sqlite3.Row]:
    with db() as con:
        return con.execute(
            "SELECT * FROM posts WHERE job_id = ? ORDER BY sort_order", (job_id,)
        ).fetchall()


def update_job(job_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with db() as con:
        con.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)


def run_job(job_id: int) -> None:
    row = get_job(job_id)
    if not row:
        return
    update_job(job_id, status="running", stage="Starting", error=None, finished_at=None)

    def progress(msg: str, current: int = 0, total: int = 0) -> None:
        update_job(
            job_id,
            stage=msg,
            progress_current=int(current or 0),
            progress_total=int(total or 0),
        )

    out = job_dir(job_id)
    bearer = get_setting("bearer") or None
    try:
        result = book.build_book(
            user=row["handle"],
            out=out,
            ids_text=row["ids_text"] or "",
            since=row["since"] or None,
            until=row["until"] or None,
            classify_flag=bool(row["classify"]),
            limit=int(row["limit_n"] or 0),
            bearer=bearer,
            progress=progress,
        )
        posts = result["posts"]
        with db() as con:
            con.execute("DELETE FROM posts WHERE job_id = ?", (job_id,))
            for i, p in enumerate(posts, 1):
                img = p.get("image_path") or ""
                rel = str(Path(img).name) if img else ""
                con.execute(
                    """INSERT INTO posts(
                        job_id, tweet_id, handle, name, date, text, url, media,
                        rec, kind, why, image_relpath, sort_order
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        p.get("id"),
                        p.get("handle"),
                        p.get("name"),
                        p.get("date"),
                        p.get("text"),
                        p.get("url"),
                        p.get("media"),
                        p.get("rec"),
                        p.get("kind"),
                        p.get("why"),
                        rel,
                        i,
                    ),
                )
        keep = sum(1 for p in posts if p.get("rec") == "KEEP")
        delete = sum(1 for p in posts if p.get("rec") == "DELETE")
        update_job(
            job_id,
            status="done",
            stage="Done",
            post_count=len(posts),
            keep_count=keep,
            delete_count=delete,
            finished_at=utcnow(),
            progress_current=len(posts),
            progress_total=len(posts),
        )
    except Exception as e:
        update_job(job_id, status="error", stage="Failed", error=str(e), finished_at=utcnow())


def start_job_thread(job_id: int) -> None:
    t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    t.start()


def posts_as_dicts(job_id: int) -> list[dict]:
    out = job_dir(job_id)
    rows = list_posts(job_id)
    posts = []
    for r in rows:
        img = out / "images" / r["image_relpath"] if r["image_relpath"] else None
        posts.append(
            {
                "id": r["tweet_id"],
                "handle": r["handle"],
                "name": r["name"],
                "date": r["date"],
                "text": r["text"],
                "url": r["url"],
                "media": r["media"],
                "rec": r["rec"],
                "kind": r["kind"],
                "why": r["why"],
                "image_path": str(img) if img else "",
            }
        )
    return posts


def bookmarklet_href() -> str:
    src = (ROOT / "static" / "collect.js").read_text(encoding="utf-8")
    src = src.replace("__APP__", request.host_url)
    return "javascript:" + quote(src, safe="")


@app.route("/")
def index():
    return render_template(
        "index.html",
        jobs=list_jobs(),
        has_bearer=bool(get_setting("bearer")),
        bookmarklet=bookmarklet_href(),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        token = (request.form.get("bearer") or "").strip()
        set_setting("bearer", token)
        flash("Saved. Token stays in the local SQLite database on this machine.")
        return redirect(url_for("settings"))
    return render_template("settings.html", has_bearer=bool(get_setting("bearer")))


@app.post("/jobs")
def create_job():
    handle = (request.form.get("handle") or "").strip().lstrip("@")
    if not handle:
        flash("Need an X handle.")
        return redirect(url_for("index"))
    ids_text = (request.form.get("ids_text") or "").strip()
    since = (request.form.get("since") or "").strip() or None
    until = (request.form.get("until") or "").strip() or None
    classify = 1 if request.form.get("classify") else 0
    try:
        limit_n = int(request.form.get("limit") or 0)
    except ValueError:
        limit_n = 0
    if not ids_text and not get_setting("bearer"):
        flash("Paste tweet URLs, or add an X API token in Settings to list a whole account.")
        return redirect(url_for("index"))
    with db() as con:
        cur = con.execute(
            """INSERT INTO jobs(handle, since, until, ids_text, classify, limit_n, status, stage, created_at)
               VALUES (?,?,?,?,?,?, 'queued', 'Queued', ?)""",
            (handle, since, until, ids_text, classify, limit_n, utcnow()),
        )
        job_id = cur.lastrowid
    start_job_thread(job_id)
    return redirect(url_for("job_page", job_id=job_id))


@app.get("/jobs/<int:job_id>")
def job_page(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    return render_template("job.html", job=job, posts=list_posts(job_id))


@app.get("/api/jobs/<int:job_id>")
def job_api(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    return jsonify(dict(job))


@app.post("/jobs/<int:job_id>/retry")
def retry_job(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    if job["status"] == "running":
        flash("That job is still running.")
        return redirect(url_for("job_page", job_id=job_id))
    start_job_thread(job_id)
    return redirect(url_for("job_page", job_id=job_id))


@app.post("/jobs/<int:job_id>/posts/<tweet_id>/rec")
def set_rec(job_id: int, tweet_id: str):
    if request.is_json:
        rec = ((request.json or {}).get("rec") or "").upper()
    else:
        rec = (request.form.get("rec") or "").upper()
    if rec not in ("KEEP", "DELETE"):
        abort(400)
    with db() as con:
        con.execute(
            "UPDATE posts SET rec = ? WHERE job_id = ? AND tweet_id = ?",
            (rec, job_id, tweet_id),
        )
        rows = con.execute("SELECT rec FROM posts WHERE job_id = ?", (job_id,)).fetchall()
        keep = sum(1 for r in rows if r["rec"] == "KEEP")
        delete = sum(1 for r in rows if r["rec"] == "DELETE")
        con.execute(
            "UPDATE jobs SET keep_count = ?, delete_count = ? WHERE id = ?",
            (keep, delete, job_id),
        )
    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "rec": rec})
    return redirect(url_for("job_page", job_id=job_id))


@app.post("/jobs/<int:job_id>/rebuild")
def rebuild_pdf(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    posts = posts_as_dicts(job_id)
    if not posts:
        flash("No posts to rebuild.")
        return redirect(url_for("job_page", job_id=job_id))
    out = job_dir(job_id)
    avatar = out / "avatar.png"
    img_dir = out / "images"
    try:
        book.write_markdown(posts, job["handle"], out, job["since"] or "", job["until"] or "")
        book.write_pdf(
            posts, job["handle"], out, img_dir, avatar,
            job["since"] or "", job["until"] or "",
        )
        flash("PDF rebuilt from the current KEEP/DELETE flags.")
    except Exception as e:
        flash(f"Rebuild failed: {e}")
    return redirect(url_for("job_page", job_id=job_id))


@app.get("/jobs/<int:job_id>/pdf")
def download_pdf(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    path = job_dir(job_id) / f"{job['handle']}-photo-book.pdf"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True)


@app.get("/jobs/<int:job_id>/md")
def download_md(job_id: int):
    job = get_job(job_id)
    if not job:
        abort(404)
    path = job_dir(job_id) / f"{job['handle']}-photo-book.md"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True)


@app.get("/jobs/<int:job_id>/images/<name>")
def job_image(job_id: int, name: str):
    if "/" in name or name.startswith("."):
        abort(404)
    path = job_dir(job_id) / "images" / name
    if not path.exists():
        abort(404)
    return send_file(path)


init_db()


def main() -> None:
    print("x-photo-book  http://127.0.0.1:5055")
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)


if __name__ == "__main__":
    main()

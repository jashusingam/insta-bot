"""
download_handler.py — Download engine for Instagram and Twitter/X
Handles videos, single photos, carousels, daily quota, progress, and cleanup.

Key design for images:
  yt-dlp is used ONLY to extract direct URLs (skip_download=True).
  httpx then downloads the image bytes directly.
  This avoids the "No video formats found" error that occurs when yt-dlp's
  download path tries to apply video format selection to a photo post.
"""

import os
import asyncio
import logging
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
import yt_dlp
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}

QUALITY_PROFILES = {
    "hd": {
        "label" : "⬇️  HD Quality (Max 720p)",
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "ext"   : "mp4",
        "width" : None,
        "height": 720,
    },
    "sd": {
        "label" : "⬇️  SD Quality (Max 360p)",
        "format": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
        "ext"   : "mp4",
        "width" : None,
        "height": 360,
    },
    "audio": {
        "label" : "🎵  Audio only",
        "format": "bestaudio/best",
        "ext"   : "mp3",
        "width" : None,
        "height": None,
    },
}

_COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept"         : "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _best_image_url(entry: dict) -> tuple:
    """
    Extract the best direct image URL and file extension from a yt-dlp info entry.
    Returns (url: str | None, ext: str).
    Priority: image-typed format → top-level url → thumbnail.
    """
    # 1. Look through formats for an image-typed one, pick highest quality
    formats = entry.get("formats") or []
    image_fmts = [
        f for f in formats
        if (f.get("ext") or "").lower() in IMAGE_EXTS and f.get("url")
    ]
    if image_fmts:
        best = max(
            image_fmts,
            key=lambda f: (
                f.get("filesize") or
                f.get("filesize_approx") or
                (f.get("width") or 0) * (f.get("height") or 0) or
                0
            ),
        )
        return best["url"], (best.get("ext") or "jpg").lower()

    # 2. Top-level URL whose extension looks like an image
    top_url = entry.get("url") or ""
    top_ext = (entry.get("ext") or "").lower()
    if top_url and top_ext in IMAGE_EXTS:
        return top_url, top_ext
    if top_url:
        url_ext = top_url.split("?")[0].rsplit(".", 1)[-1].lower()
        if url_ext in IMAGE_EXTS:
            return top_url, url_ext

    # 3. Fall back to thumbnail (lower res but guaranteed to be an image)
    thumbnail = entry.get("thumbnail") or ""
    if thumbnail:
        thumb_ext = thumbnail.split("?")[0].rsplit(".", 1)[-1].lower()
        if thumb_ext not in IMAGE_EXTS:
            thumb_ext = "jpg"
        logger.info("Using thumbnail as image source (best available for this entry)")
        return thumbnail, thumb_ext

    return None, "jpg"


class DownloadHandler:

    def __init__(
        self,
        downloads_dir: str  = "downloads",
        cookies_file: str   = "downloads/cookies.txt",
        max_daily_mb: float = 1024.0,
    ):
        self.downloads_dir = Path(downloads_dir)
        self.cookies_file  = cookies_file
        self.max_daily_mb  = max_daily_mb

        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self._usage: dict[int, dict] = {}
        self._active: dict[int, yt_dlp.YoutubeDL] = {}

        self._usage_file = self.downloads_dir / ".usage.json"
        self._load_usage()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_daily_usage(self, uid: int) -> float:
        self._ensure_fresh(uid)
        return self._usage.get(uid, {}).get("used_mb", 0.0)

    def cancel_download(self, uid: int) -> bool:
        ydl = self._active.pop(uid, None)
        if ydl:
            try:
                ydl.stop_download()
            except Exception:
                pass
            return True
        return False

    def build_quality_keyboard(self, meta: dict, uid: int) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(profile["label"], callback_data=f"dl:{key}")]
            for key, profile in QUALITY_PROFILES.items()
        ]
        rows.append([InlineKeyboardButton("❌  Cancel", callback_data="dl:cancel")])
        return InlineKeyboardMarkup(rows)

    # ── Video download ────────────────────────────────────────────────────────

    async def download(
        self,
        url: str,
        quality: str,
        user_id: int,
        cookies_file: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> dict:
        if quality == "cancel":
            raise ValueError("Download cancelled by user.")

        profile = QUALITY_PROFILES.get(quality)
        if not profile:
            raise ValueError(f"Unknown quality: {quality}")

        ts       = int(time.time())
        out_path = self.downloads_dir / f"{user_id}_{ts}.%(ext)s"

        main_loop  = asyncio.get_running_loop()
        pct_holder = [0.0]

        def _progress_hook(d):
            if not progress_callback:
                return
            if d["status"] == "downloading":
                total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                downloaded = d.get("downloaded_bytes", 0)
                pct        = min(downloaded / total * 100, 99.9)
                if pct - pct_holder[0] >= 5:
                    pct_holder[0] = pct
                    self._safe_progress(main_loop, progress_callback, pct)
            elif d["status"] == "finished":
                self._safe_progress(main_loop, progress_callback, 100.0)

        ydl_opts = {
            "format"             : profile["format"],
            "outtmpl"            : str(out_path),
            "merge_output_format": "mp4",
            "quiet"              : True,
            "no_warnings"        : True,
            "progress_hooks"     : [_progress_hook],
            "socket_timeout"     : 30,
            "retries"            : 5,
            "http_headers"       : _COMMON_HEADERS,
        }

        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts["cookiefile"] = cookies_file

        if quality == "audio":
            ydl_opts["postprocessors"] = [{
                "key"             : "FFmpegExtractAudio",
                "preferredcodec"  : "mp3",
                "preferredquality": "192",
            }]

        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._active[user_id] = ydl
                try:
                    ydl.download([url])
                finally:
                    self._active.pop(user_id, None)

        await loop.run_in_executor(None, _run)

        filepath = self._find_file(self.downloads_dir, user_id, ts, profile["ext"])
        if not filepath:
            raise FileNotFoundError("Downloaded file was not found.")

        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self._add_usage(user_id, size_mb)

        return {
            "filepath": filepath,
            "size_mb" : size_mb,
            "width"   : profile["width"],
            "height"  : profile["height"],
        }

    # ── Image / photo download ────────────────────────────────────────────────

    async def download_image(
        self,
        url: str,
        user_id: int,
        cookies_file: str,
    ) -> list:
        """
        Download image(s) from a URL without using yt-dlp's download path.

        How it works:
          1. yt-dlp extracts_info (skip_download=True) → gives us direct image URLs
          2. httpx downloads each image URL directly
          This completely avoids yt-dlp's format-selection/merging logic, which
          fails on photo posts with "No video formats found."

        Returns list of {filepath, size_mb} dicts (one per image in a carousel).
        """
        ts = int(time.time())

        # ── Step 1: extract direct URL(s) via yt-dlp ─────────────────────────
        ydl_opts = {
            "quiet"         : True,
            "no_warnings"   : True,
            "skip_download" : True,   # <-- critical: do NOT attempt to download
            "socket_timeout": 20,
            "http_headers"  : _COMMON_HEADERS,
        }
        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts["cookiefile"] = cookies_file

        loop = asyncio.get_event_loop()

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await loop.run_in_executor(None, _extract)

        # Collect all entries (playlist = carousel, single entry = one photo)
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
        else:
            entries = [info]

        if not entries:
            raise ValueError("No media entries found for this URL.")

        # ── Step 2: download each image directly with httpx ───────────────────
        results = []

        async with httpx.AsyncClient(
            headers=_COMMON_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            for i, entry in enumerate(entries):
                img_url, ext = _best_image_url(entry)

                if not img_url:
                    logger.warning("No image URL found for entry %d — skipping", i + 1)
                    continue

                filepath = self.downloads_dir / f"{user_id}_{ts}_{i+1:02d}.{ext}"

                try:
                    resp = await client.get(img_url)
                    resp.raise_for_status()
                    filepath.write_bytes(resp.content)

                    size_mb = filepath.stat().st_size / (1024 * 1024)
                    self._add_usage(user_id, size_mb)
                    results.append({"filepath": str(filepath), "size_mb": size_mb})
                    logger.info(
                        "Image %d/%d downloaded — %.2f MB", i + 1, len(entries), size_mb
                    )

                except Exception as exc:
                    logger.error("Failed to download image %d: %s", i + 1, exc)

        if not results:
            raise FileNotFoundError(
                "No images could be downloaded from this post.\n"
                "If it's a private account, add login cookies and try again."
            )

        return results

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _safe_progress(
        loop: asyncio.AbstractEventLoop,
        callback: Callable[[float], None],
        pct: float,
    ) -> None:
        try:
            loop.call_soon_threadsafe(callback, pct)
        except Exception as exc:
            logger.warning("Could not schedule progress update: %s", exc)

    def cleanup(self, filepath: Optional[str]) -> None:
        if filepath and os.path.isfile(filepath):
            try:
                os.remove(filepath)
                logger.info("Cleaned up %s", filepath)
            except Exception as exc:
                logger.warning("Cleanup failed for %s: %s", filepath, exc)

    def cleanup_list(self, results: list) -> None:
        for r in results:
            self.cleanup(r.get("filepath"))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _find_file(self, directory: Path, uid: int, ts: int, ext: str) -> Optional[str]:
        prefix = f"{uid}_{ts}"
        for f in directory.iterdir():
            if f.stem == prefix or f.name.startswith(prefix):
                return str(f)
        for f in sorted(
            directory.glob(f"*.{ext}"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            if str(uid) in f.name:
                return str(f)
        return None

    def _ensure_fresh(self, uid: int) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rec   = self._usage.get(uid)
        if not rec or rec["date"] != today:
            self._usage[uid] = {"date": today, "used_mb": 0.0}

    def _add_usage(self, uid: int, mb: float) -> None:
        self._ensure_fresh(uid)
        self._usage[uid]["used_mb"] += mb
        self._save_usage()

    def _load_usage(self) -> None:
        if self._usage_file.exists():
            try:
                with open(self._usage_file) as f:
                    raw = json.load(f)
                self._usage = {int(k): v for k, v in raw.items()}
                logger.info("Loaded usage data for %d users.", len(self._usage))
            except Exception as exc:
                logger.warning("Could not load usage file: %s", exc)

    def _save_usage(self) -> None:
        try:
            with open(self._usage_file, "w") as f:
                json.dump(self._usage, f, indent=2)
        except Exception as exc:
            logger.warning("Could not save usage file: %s", exc)

"""
download_handler.py — Download engine for Instagram and Twitter/X
Handles videos, single photos, carousels, daily quota, progress, and cleanup.
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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}

# ─── Quality Profiles (video only) ───────────────────────────────────────────
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


class DownloadHandler:
    """
    Manages downloads, per-user daily quotas, progress reporting,
    active-download tracking for cancellation, and file cleanup.
    """

    def __init__(
        self,
        downloads_dir: str = "downloads",
        cookies_file: str  = "downloads/cookies.txt",
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
        rows = []
        for key, profile in QUALITY_PROFILES.items():
            rows.append([InlineKeyboardButton(profile["label"], callback_data=f"dl:{key}")])
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
        """Download a video/audio file. Returns dict with filepath, size_mb, width, height."""
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
        Download image(s) from a URL.
        Works for single photos and carousels (multiple images).
        Returns a list of dicts: [{filepath, size_mb}, ...]
        """
        ts       = int(time.time())
        # Use playlist_index in the template so carousels get numbered files
        out_path = self.downloads_dir / f"{user_id}_{ts}_%(playlist_index)s.%(ext)s"

        ydl_opts = {
            # 'best' picks the highest-res image without trying to merge streams
            "format"        : "best",
            "outtmpl"       : str(out_path),
            "quiet"         : True,
            "no_warnings"   : True,
            "socket_timeout": 30,
            "retries"       : 5,
            "http_headers"  : _COMMON_HEADERS,
            # Don't ask yt-dlp to merge or post-process images
            "postprocessors": [],
        }

        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts["cookiefile"] = cookies_file

        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._active[user_id] = ydl
                try:
                    ydl.download([url])
                finally:
                    self._active.pop(user_id, None)

        await loop.run_in_executor(None, _run)

        # Collect all image files that match our prefix
        prefix  = f"{user_id}_{ts}_"
        results = []

        for f in sorted(self.downloads_dir.iterdir(), key=lambda x: x.stat().st_mtime):
            if f.name.startswith(prefix) and f.suffix.lstrip(".").lower() in IMAGE_EXTS:
                size_mb = f.stat().st_size / (1024 * 1024)
                self._add_usage(user_id, size_mb)
                results.append({"filepath": str(f), "size_mb": size_mb})

        # Fallback: single image whose name starts with user_id_ts (no playlist index)
        if not results:
            single_prefix = f"{user_id}_{ts}"
            for f in sorted(
                self.downloads_dir.iterdir(),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )[:20]:
                if f.name.startswith(single_prefix) and f.suffix.lstrip(".").lower() in IMAGE_EXTS:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    self._add_usage(user_id, size_mb)
                    results.append({"filepath": str(f), "size_mb": size_mb})
                    break

        if not results:
            raise FileNotFoundError(
                "No image file was found after download. "
                "The post may be a video or the link may be unsupported."
            )

        logger.info("Downloaded %d image(s) for uid=%d", len(results), user_id)
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
        """Cleanup a list of result dicts (used after carousel sends)."""
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

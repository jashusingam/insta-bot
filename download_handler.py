"""
download_handler.py — Advanced download engine for Instagram Reel Bot
Handles quality selection, daily quota tracking, progress, and cleanup.
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

# ─── Quality Profiles ────────────────────────────────────────────────────────
QUALITY_PROFILES = {
    "1088x720": {
        "label": "⬇️  1088×720  HD   (~1.4 MB)",
        "format": "bestvideo[width<=1088][height<=720]+bestaudio/best[width<=1088]",
        "ext": "mp4",
        "width": 1088,
        "height": 720,
    },
    "544x360": {
        "label": "⬇️  544×360   SD   (~0.75 MB)",
        "format": "bestvideo[width<=544][height<=360]+bestaudio/best[width<=544]",
        "ext": "mp4",
        "width": 544,
        "height": 360,
    },
    "480x854": {
        "label": "⬇️  480×854  Story (~0.48 MB)",
        "format": "bestvideo[width<=480]+bestaudio/best[height<=854]",
        "ext": "mp4",
        "width": 480,
        "height": 854,
    },
    "audio": {
        "label": "🎵  Audio only      (~0.24 MB)",
        "format": "bestaudio/best",
        "ext": "mp3",
        "width": None,
        "height": None,
    },
}


class DownloadHandler:
    """
    Manages downloads, per-user daily quotas, progress reporting,
    active-download tracking for cancellation, and file cleanup.
    """

    def __init__(
        self,
        downloads_dir: str = "downloads",
        cookies_file: str = "downloads/cookies.txt",
        max_daily_mb: float = 1024.0,
    ):
        self.downloads_dir = Path(downloads_dir)
        self.cookies_file  = cookies_file
        self.max_daily_mb  = max_daily_mb

        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        # { uid: {"date": "YYYY-MM-DD", "used_mb": float} }
        self._usage: dict[int, dict] = {}

        # Active download processes { uid: yt_dlp YoutubeDL instance }
        self._active: dict[int, yt_dlp.YoutubeDL] = {}

        # Load persisted usage from disk
        self._usage_file = self.downloads_dir / ".usage.json"
        self._load_usage()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_daily_usage(self, uid: int) -> float:
        """Return MB used today by this user."""
        self._ensure_fresh(uid)
        return self._usage.get(uid, {}).get("used_mb", 0.0)

    def cancel_download(self, uid: int) -> bool:
        """Signal the active download for uid to stop."""
        ydl = self._active.pop(uid, None)
        if ydl:
            try:
                ydl._request_director  # triggers cleanup in yt-dlp
            except Exception:
                pass
            return True
        return False

    def build_quality_keyboard(self, meta: dict, uid: int) -> InlineKeyboardMarkup:
        """Build an inline keyboard with all quality options."""
        used = self.get_daily_usage(uid)
        rows = []
        for key, profile in QUALITY_PROFILES.items():
            rows.append([InlineKeyboardButton(profile["label"], callback_data=f"dl:{key}")])
        rows.append([InlineKeyboardButton("❌  Cancel", callback_data="dl:cancel")])
        return InlineKeyboardMarkup(rows)

    async def download(
        self,
        url: str,
        quality: str,
        user_id: int,
        cookies_file: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> dict:
        """
        Download the video at `url` using the chosen quality profile.
        Returns a dict with filepath, size_mb, width, height.
        Raises on error.
        """
        if quality == "cancel":
            raise ValueError("Download cancelled by user.")

        profile = QUALITY_PROFILES.get(quality)
        if not profile:
            raise ValueError(f"Unknown quality: {quality}")

        ts        = int(time.time())
        out_name  = f"{user_id}_{ts}.%(ext)s"
        out_path  = self.downloads_dir / out_name

        # Capture the currently-running event loop BEFORE we hop into a worker
        # thread. yt-dlp's progress_hooks fire from inside run_in_executor's
        # background thread, which has no event loop of its own — so any
        # asyncio call there (including asyncio.create_task) would raise
        # "no running event loop". We hand work back to the real loop via
        # run_coroutine_threadsafe instead.
        main_loop = asyncio.get_running_loop()

        # Closure to track progress
        pct_holder = [0.0]

        def _progress_hook(d):
            if not progress_callback:
                return
            if d["status"] == "downloading":
                total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                downloaded = d.get("downloaded_bytes", 0)
                pct        = min(downloaded / total * 100, 99.9)
                if pct - pct_holder[0] >= 5:  # update every 5 %
                    pct_holder[0] = pct
                    self._safe_progress(main_loop, progress_callback, pct)
            elif d["status"] == "finished":
                self._safe_progress(main_loop, progress_callback, 100.0)

        ydl_opts = {
            "format"        : profile["format"],
            "outtmpl"       : str(out_path),
            "merge_output_format": "mp4",
            "quiet"         : True,
            "no_warnings"   : True,
            "progress_hooks": [_progress_hook],
            "socket_timeout": 30,
            "retries"       : 5,
            "http_headers"  : {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        }

        # Add cookies if file exists
        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts["cookiefile"] = cookies_file

        # Audio-only post-processing
        if quality == "audio":
            ydl_opts["postprocessors"] = [{
                "key"            : "FFmpegExtractAudio",
                "preferredcodec" : "mp3",
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

        # Locate the downloaded file
        ext      = profile["ext"]
        filepath = self._find_file(self.downloads_dir, user_id, ts, ext)
        if not filepath:
            raise FileNotFoundError("Downloaded file not found on disk.")

        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self._add_usage(user_id, size_mb)

        return {
            "filepath": filepath,
            "size_mb" : size_mb,
            "width"   : profile["width"],
            "height"  : profile["height"],
        }

    @staticmethod
    def _safe_progress(loop: asyncio.AbstractEventLoop, callback: Callable[[float], None], pct: float) -> None:
        """
        Called from yt-dlp's worker thread. Schedules `callback(pct)` onto the
        real event loop thread-safely instead of calling asyncio APIs directly
        from a thread that has no event loop of its own.
        """
        try:
            loop.call_soon_threadsafe(callback, pct)
        except Exception as exc:
            logger.warning("Could not schedule progress update: %s", exc)

    def cleanup(self, filepath: Optional[str]) -> None:
        """Delete a downloaded file after sending."""
        if filepath and os.path.isfile(filepath):
            try:
                os.remove(filepath)
                logger.info("Cleaned up %s", filepath)
            except Exception as exc:
                logger.warning("Cleanup failed for %s: %s", filepath, exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _find_file(self, directory: Path, uid: int, ts: int, ext: str) -> Optional[str]:
        """Glob for the output file (extension may vary)."""
        prefix = f"{uid}_{ts}"
        for f in directory.iterdir():
            if f.stem == prefix or f.name.startswith(prefix):
                return str(f)
        # Fallback: search by ext
        for f in sorted(directory.glob(f"*.{ext}"), key=lambda x: x.stat().st_mtime, reverse=True):
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
                # Convert keys back to int
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
"""
metadata_handler.py — Fetch and format video metadata (Instagram + Twitter/X)
"""

import re
import asyncio
import logging
from typing import Optional
import yt_dlp

logger = logging.getLogger(__name__)

# Supported Instagram URL patterns
INSTAGRAM_PATTERNS = [
    r"https?://(www\.)?instagram\.com/(reel|p|tv|share)/[\w\-]+/?",
    r"https?://instagr\.am/(reel|p|tv|share)/[\w\-]+/?",
]

# Supported Twitter / X URL patterns
TWITTER_PATTERNS = [
    r"https?://(www\.)?(twitter|x)\.com/\w+/status/\d+",
    r"https?://(www\.)?(twitter|x)\.com/i/status/\d+",
    r"https?://t\.co/[\w]+",  # Twitter's shortlink domain
]


class MetadataHandler:
    """Validates Instagram/Twitter URLs and fetches video metadata via yt-dlp."""

    def is_instagram_url(self, text: str) -> bool:
        """Return True if text contains a recognisable Instagram media URL."""
        for pattern in INSTAGRAM_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def is_twitter_url(self, text: str) -> bool:
        """Return True if text contains a recognisable Twitter/X media URL."""
        for pattern in TWITTER_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def is_supported_url(self, text: str) -> bool:
        """Return True if the text contains any URL this bot knows how to handle."""
        return self.is_instagram_url(text) or self.is_twitter_url(text)

    def detect_platform(self, text: str) -> str:
        """Return 'instagram', 'twitter', or 'unknown' for the given text."""
        if self.is_instagram_url(text):
            return "instagram"
        if self.is_twitter_url(text):
            return "twitter"
        return "unknown"

    async def fetch_metadata(self, url: str, cookies_file: Optional[str] = None) -> dict:
        """
        Fetch metadata for an Instagram reel/post or Twitter status video.
        Returns a normalised dict with title, uploader, duration, thumbnail, formats, etc.
        Raises on failure.
        """
        ydl_opts = {
            "quiet"       : True,
            "no_warnings" : True,
            "skip_download": True,
            "socket_timeout": 20,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        }
        if cookies_file:
            import os
            if os.path.isfile(cookies_file):
                ydl_opts["cookiefile"] = cookies_file

        loop = asyncio.get_event_loop()

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await loop.run_in_executor(None, _extract)
        return self._normalise(info)

    # ── Private ───────────────────────────────────────────────────────────────

    def _normalise(self, info: dict) -> dict:
        """Convert raw yt-dlp info dict into a clean summary."""
        duration = info.get("duration", 0) or 0
        return {
            "title"       : self._clean_title(info.get("title") or info.get("description") or "Video"),
            "uploader"    : info.get("uploader") or info.get("channel") or "unknown",
            "uploader_id" : info.get("uploader_id") or "",
            "duration"    : duration,
            "duration_str": self._fmt_duration(duration),
            "thumbnail"   : info.get("thumbnail") or "",
            "like_count"  : info.get("like_count"),
            "view_count"  : info.get("view_count"),
            "comment_count": info.get("comment_count"),
            "upload_date" : self._fmt_date(info.get("upload_date") or ""),
            "webpage_url" : info.get("webpage_url") or "",
            "formats"     : self._summarise_formats(info.get("formats") or []),
        }

    @staticmethod
    def _clean_title(title: str, max_len: int = 80) -> str:
        title = title.replace("\n", " ").strip()
        return title[:max_len] + "…" if len(title) > max_len else title

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_date(raw: str) -> str:
        """Convert 'YYYYMMDD' → 'DD Mon YYYY'."""
        if len(raw) == 8:
            try:
                from datetime import datetime
                return datetime.strptime(raw, "%Y%m%d").strftime("%d %b %Y")
            except ValueError:
                pass
        return raw

    @staticmethod
    def _summarise_formats(formats: list) -> list:
        """Return a compact list of available format summaries."""
        seen, out = set(), []
        for f in formats:
            w = f.get("width")
            h = f.get("height")
            ext = f.get("ext", "?")
            key = f"{w}x{h}"
            if key not in seen and w and h:
                seen.add(key)
                out.append({
                    "resolution": key,
                    "ext"       : ext,
                    "filesize"  : f.get("filesize") or f.get("filesize_approx"),
                })
        return out

    # ── Message formatter ─────────────────────────────────────────────────────

    @staticmethod
    def format_meta_message(meta: dict, used_mb: float, max_mb: float) -> str:
        """Build the HTML caption shown above the quality keyboard."""
        lines = [
            f"🎬 <b>{meta['title']}</b>",
            f"👤  @{meta['uploader']}",
        ]
        if meta["duration"]:
            lines.append(f"⏱  Duration: <code>{meta['duration_str']}</code>")
        if meta["upload_date"]:
            lines.append(f"📅  Uploaded: {meta['upload_date']}")
        if meta["view_count"] is not None:
            lines.append(f"👁  Views: {meta['view_count']:,}")
        if meta["like_count"] is not None:
            lines.append(f"❤️  Likes: {meta['like_count']:,}")

        remaining = max(0, max_mb - used_mb)
        lines += [
            "",
            f"📊  Daily quota: <b>{used_mb:.1f} / {max_mb} MB</b>  (left: {remaining:.1f} MB)",
            "",
            "🎚  <b>Choose quality:</b>",
        ]
        return "\n".join(lines)

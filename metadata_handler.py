"""
metadata_handler.py — Fetch and format video/photo metadata (Instagram + Twitter/X)
"""

import re
import asyncio
import logging
from typing import Optional
import yt_dlp

logger = logging.getLogger(__name__)

INSTAGRAM_PATTERNS = [
    r"https?://(www\.)?instagram\.com/(reel|p|tv|share)/[\w\-]+/?",
    r"https?://instagr\.am/(reel|p|tv|share)/[\w\-]+/?",
]

TWITTER_PATTERNS = [
    r"https?://(www\.)?(twitter|x)\.com/\w+/status/\d+",
    r"https?://(www\.)?(twitter|x)\.com/i/status/\d+",
    r"https?://t\.co/[\w]+",
]

IMAGE_EXTS   = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
VIDEO_EXTS   = {'mp4', 'webm', 'mkv', 'mov', 'avi', 'flv', 'm4v'}

_COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


class MetadataHandler:

    def is_instagram_url(self, text: str) -> bool:
        return any(re.search(p, text) for p in INSTAGRAM_PATTERNS)

    def is_twitter_url(self, text: str) -> bool:
        return any(re.search(p, text) for p in TWITTER_PATTERNS)

    def is_supported_url(self, text: str) -> bool:
        return self.is_instagram_url(text) or self.is_twitter_url(text)

    def detect_platform(self, text: str) -> str:
        if self.is_instagram_url(text):
            return "instagram"
        if self.is_twitter_url(text):
            return "twitter"
        return "unknown"

    async def fetch_metadata(self, url: str, cookies_file: Optional[str] = None) -> dict:
        ydl_opts = {
            "quiet"                 : True,
            "no_warnings"           : True,
            "skip_download"         : True,
            "socket_timeout"        : 20,
            "http_headers"          : _COMMON_HEADERS,
            # Don't raise an error when a post has no video formats (e.g. photo posts).
            # yt-dlp still returns the full info dict — we detect is_photo from ext/formats.
            "ignore_no_formats_error": True,
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
        duration = info.get("duration", 0) or 0
        is_photo, photo_count = self._detect_photo(info)

        return {
            "title"       : self._clean_title(info.get("title") or info.get("description") or "Media"),
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
            "is_photo"    : is_photo,
            "photo_count" : photo_count,
        }

    @staticmethod
    def _detect_photo(info: dict) -> tuple:
        """
        Determine if the yt-dlp info dict represents a photo/image (vs a video).
        Returns (is_photo: bool, count: int).

        Detection order (most → least reliable):
          1. Top-level 'ext' is an image extension
          2. Top-level URL itself has an image extension
          3. Formats list has no video codec
          4. Playlist where entries look like images
        """

        # ── Carousel / playlist ───────────────────────────────────────────────
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            image_entries = 0
            for entry in entries:
                ext     = (entry.get("ext") or "").lower()
                url_str = entry.get("url") or ""
                url_ext = url_str.split("?")[0].rsplit(".", 1)[-1].lower() if "." in url_str else ""
                formats = entry.get("formats") or []
                has_real_video = any(
                    f.get("vcodec", "none") not in ("none", None, "")
                    for f in formats
                )
                is_video_ext = ext in VIDEO_EXTS
                is_image_ext = ext in IMAGE_EXTS or url_ext in IMAGE_EXTS

                if is_image_ext or (not has_real_video and not is_video_ext):
                    image_entries += 1

            if image_entries > 0:
                return True, image_entries
            return False, 0

        # ── Single entry ──────────────────────────────────────────────────────

        # 1. Top-level ext (most reliable signal for Instagram photos)
        ext = (info.get("ext") or "").lower()
        if ext in IMAGE_EXTS:
            return True, 1
        if ext in VIDEO_EXTS:
            return False, 0

        # 2. Top-level URL has an image extension
        top_url = info.get("url") or ""
        if top_url:
            url_ext = top_url.split("?")[0].rsplit(".", 1)[-1].lower()
            if url_ext in IMAGE_EXTS:
                return True, 1

        # 3. Formats: if none have a real video codec → photo
        formats = info.get("formats") or []
        if formats:
            has_real_video = any(
                f.get("vcodec", "none") not in ("none", None, "")
                for f in formats
            )
            if not has_real_video:
                return True, 1

        # 4. No formats at all and not a known video ext → assume photo
        if not formats and ext not in VIDEO_EXTS:
            return True, 1

        return False, 0

    @staticmethod
    def _clean_title(title: str, max_len: int = 80) -> str:
        title = title.replace("\n", " ").strip()
        return title[:max_len] + "…" if len(title) > max_len else title

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def _fmt_date(raw: str) -> str:
        if len(raw) == 8:
            try:
                from datetime import datetime
                return datetime.strptime(raw, "%Y%m%d").strftime("%d %b %Y")
            except ValueError:
                pass
        return raw

    @staticmethod
    def _summarise_formats(formats: list) -> list:
        seen, out = set(), []
        for f in formats:
            w, h  = f.get("width"), f.get("height")
            ext   = f.get("ext", "?")
            key   = f"{w}x{h}"
            if key not in seen and w and h:
                seen.add(key)
                out.append({
                    "resolution": key,
                    "ext"       : ext,
                    "filesize"  : f.get("filesize") or f.get("filesize_approx"),
                })
        return out

    # ── Message formatters ────────────────────────────────────────────────────

    @staticmethod
    def format_meta_message(meta: dict, used_mb: float, max_mb: float) -> str:
        lines = [f"🎬 <b>{meta['title']}</b>", f"👤  @{meta['uploader']}"]
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

    @staticmethod
    def format_photo_message(meta: dict, used_mb: float, max_mb: float) -> str:
        count = meta.get("photo_count", 1)
        label = f"{count} photo{'s' if count > 1 else ''}"
        lines = [
            f"🖼  <b>{meta['title']}</b>",
            f"👤  @{meta['uploader']}",
            f"📸  {label} detected",
        ]
        if meta["upload_date"]:
            lines.append(f"📅  Uploaded: {meta['upload_date']}")
        if meta["like_count"] is not None:
            lines.append(f"❤️  Likes: {meta['like_count']:,}")
        remaining = max(0, max_mb - used_mb)
        lines += [
            "",
            f"📊  Daily quota: <b>{used_mb:.1f} / {max_mb} MB</b>  (left: {remaining:.1f} MB)",
            "",
            "⏳  Downloading…",
        ]
        return "\n".join(lines)

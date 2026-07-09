"""
download_handler.py - Download videos AND photos from Instagram, Twitter/X
Properly separates video and image handling
"""

import subprocess
import os
import logging
from pathlib import Path
from typing import Optional, Union
import time
import asyncio

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)


class MediaDownloader:
    """Handle downloads from Instagram, Twitter, YouTube, and other platforms"""
    
    def __init__(self, download_dir: Path = DOWNLOADS_DIR):
        self.download_dir = download_dir
        self.download_dir.mkdir(exist_ok=True)
    
    def detect_media_type(self, url: str) -> str:
        """
        Detect if URL contains video or photo
        Returns: 'video', 'photo', or 'unknown'
        """
        try:
            timestamp = int(time.time())
            
            # Use yt-dlp to extract info (without downloading)
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--skip-download',
                '--ignore-no-formats-error',
                '--socket-timeout', '10',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                url
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0:
                import json
                try:
                    # Clean output in case of multiple lines (playlists)
                    lines = [line.strip() for line in result.stdout.split('\n') if line.strip()]
                    if not lines:
                        return 'unknown'
                    
                    info = json.loads(lines[0])
                    
                    # Check for carousel/playlist structure first
                    if info.get('_type') == 'playlist' or 'entries' in info:
                        entries = info.get('entries', [])
                        if entries and entries[0]:
                            info = entries[0]
                    
                    formats = info.get('formats', [])
                    has_video = False
                    has_image = False
                    
                    for fmt in formats:
                        vcodec = fmt.get('vcodec', 'none')
                        ext = fmt.get('ext', '').lower()
                        
                        if vcodec and vcodec != 'none':
                            has_video = True
                        
                        if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                            has_image = True
                    
                    ext = info.get('ext', '').lower()
                    if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                        return 'photo'
                    
                    if has_video:
                        return 'video'
                    if has_image:
                        return 'photo'
                    
                    # Fallback check on URL keywords
                    if '/p/' in url or '/photo/' in url:
                        return 'photo'
                        
                    return 'video'
                    
                except json.JSONDecodeError:
                    logger.warning("Could not parse yt-dlp JSON output")
                    return 'unknown'
        
        except Exception as e:
            logger.error(f"Error detecting media type: {e}")
            return 'unknown'
    
    def download_video(self, url: str, format_type: str = 'mp4') -> Optional[str]:
        """
        Download media as MP4 video or MP3 audio
        """
        try:
            timestamp = int(time.time())
            
            if format_type == 'mp3':
                output_file = self.download_dir / f"audio_{timestamp}.mp3"
                cmd = [
                    'yt-dlp',
                    '--extract-audio',
                    '--audio-format', 'mp3',
                    '--audio-quality', '192',
                    '--format', 'bestaudio',
                    '--output', str(output_file),
                    '--quiet',
                    '--no-warnings',
                    '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    url
                ]
            else:  # mp4
                output_file = self.download_dir / f"video_{timestamp}.mp4"
                cmd = [
                    'yt-dlp',
                    '--format', 'best[ext=mp4]/best',
                    '--output', str(output_file),
                    '--quiet',
                    '--no-warnings',
                    '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    url
                ]
            
            logger.info(f"Downloading {format_type.upper()}: {url}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and output_file.exists():
                file_size = os.path.getsize(output_file)
                logger.info(f"Downloaded: {output_file} ({file_size / (1024*1024):.1f} MB)")
                return str(output_file)
            else:
                logger.error(f"Download failed: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error("Download timeout (5 minutes)")
            return None
        except Exception as e:
            logger.error(f"Video download error: {e}")
            return None
    
    def download_photo(self, url: str) -> Optional[list]:
        """
        Download photo(s) from Instagram post or Twitter post
        """
        try:
            timestamp = int(time.time())
            
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--skip-download',
                '--ignore-no-formats-error',
                '--flat-playlist',
                '--socket-timeout', '15',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                url
            ]
            
            logger.info(f"Extracting photo URLs: {url}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            photo_urls = []
            import json
            
            if result.returncode == 0:
                lines = [line.strip() for line in result.stdout.split('\n') if line.strip()]
                for line in lines:
                    try:
                        info = json.loads(line)
                        if info.get('_type') == 'playlist' or 'entries' in info:
                            for entry in info.get('entries', []):
                                if entry and entry.get('url'):
                                    photo_urls.append(entry.get('url'))
                                elif entry and entry.get('thumbnail'):
                                    photo_urls.append(entry.get('thumbnail'))
                        else:
                            # Single target item
                            url_target = info.get('url') or info.get('thumbnail')
                            if url_target:
                                photo_urls.append(url_target)
                    except Exception as e:
                        continue

            # Fallback if flat-playlist didn't yield items
            if not photo_urls:
                cmd_fallback = [
                    'yt-dlp',
                    '--dump-json',
                    '--skip-download',
                    '--ignore-no-formats-error',
                    '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    url
                ]
                res = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=30)
                if res.returncode == 0:
                    try:
                        info = json.loads(res.stdout.split('\n')[0])
                        if info.get('url'):
                            photo_urls.append(info.get('url'))
                        elif info.get('thumbnail'):
                            photo_urls.append(info.get('thumbnail'))
                    except:
                        pass
            
            if not photo_urls:
                logger.error("No photo URLs found")
                return None
            
            # Remove duplicate URLs while maintaining order
            seen = set()
            unique_photo_urls = [x for x in photo_urls if not (x in seen or seen.add(x))]
            
            downloaded_files = []
            
            for i, photo_url in enumerate(unique_photo_urls[:10]):  # Limit to 10 photos
                try:
                    ext = 'jpg'
                    if '.png' in photo_url.lower():
                        ext = 'png'
                    elif '.webp' in photo_url.lower():
                        ext = 'webp'
                    
                    file_path = self.download_dir / f"photo_{timestamp}_{i+1:02d}.{ext}"
                    
                    download_cmd = [
                        'curl',
                        '-L',
                        '-o', str(file_path),
                        '--connect-timeout', '10',
                        '--max-time', '30',
                        '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        photo_url
                    ]
                    
                    result = subprocess.run(download_cmd, capture_output=True, timeout=35)
                    
                    if result.returncode == 0 and file_path.exists() and os.path.getsize(file_path) > 1024:
                        size_mb = os.path.getsize(file_path) / (1024 * 1024)
                        logger.info(f"Downloaded photo {i+1}: {file_path} ({size_mb:.2f} MB)")
                        downloaded_files.append(str(file_path))
                    else:
                        logger.warning(f"Failed to download photo {i+1}")
                
                except Exception as e:
                    logger.error(f"Error downloading photo {i+1}: {e}")
            
            return downloaded_files if downloaded_files else None
        
        except Exception as e:
            logger.error(f"Photo download error: {e}")
            return None
    
    def download(self, url: str, format_type: str = 'mp4') -> Optional[Union[str, list]]:
        """
        Auto-detect and download (video or photo)
        """
        logger.info(f"Auto-detecting media type for: {url}")
        media_type = self.detect_media_type(url)
        logger.info(f"Detected type: {media_type}")
        
        if media_type == 'photo':
            return self.download_photo(url)
        else:
            return self.download_video(url, format_type)
    
    def cleanup(self, filepath: str) -> None:
        """Delete a file"""
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Cleaned up: {filepath}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
    
    def cleanup_list(self, filepaths: list) -> None:
        """Delete multiple files"""
        for fp in filepaths:
            self.cleanup(fp)

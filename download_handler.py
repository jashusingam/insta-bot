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
                '--socket-timeout', '10',
                url
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0:
                import json
                try:
                    info = json.loads(result.stdout)
                    
                    # Check formats for video codec
                    formats = info.get('formats', [])
                    has_video = False
                    has_image = False
                    
                    for fmt in formats:
                        vcodec = fmt.get('vcodec', 'none')
                        acodec = fmt.get('acodec', 'none')
                        ext = fmt.get('ext', '').lower()
                        
                        # Video codec present
                        if vcodec and vcodec != 'none':
                            has_video = True
                        
                        # Image extension detected
                        if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                            has_image = True
                    
                    # Check top-level extension
                    ext = info.get('ext', '').lower()
                    if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                        return 'photo'
                    
                    if has_video:
                        return 'video'
                    if has_image:
                        return 'photo'
                    
                    # Check for carousel/playlist (Instagram carousel posts)
                    if info.get('_type') == 'playlist':
                        entries = info.get('entries', [])
                        if entries:
                            first_entry = entries[0]
                            first_ext = first_entry.get('ext', '').lower()
                            if first_ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                                return 'photo'
                            return 'video'
                    
                    return 'video'  # Default to video
                    
                except json.JSONDecodeError:
                    logger.warning("Could not parse yt-dlp JSON output")
                    return 'unknown'
        
        except Exception as e:
            logger.error(f"Error detecting media type: {e}")
            return 'unknown'
    
    def download_video(self, url: str, format_type: str = 'mp4') -> Optional[str]:
        """
        Download media as MP4 video or MP3 audio
        
        Args:
            url: Direct link to Instagram video, Twitter video, YouTube, etc.
            format_type: 'mp4' for video, 'mp3' for audio
            
        Returns:
            Path to downloaded file or None if failed
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
        Download photo(s) from Instagram post
        Handles single photos and carousels (multi-image posts)
        
        Args:
            url: Instagram photo/carousel URL
            
        Returns:
            List of file paths or None if failed
        """
        try:
            timestamp = int(time.time())
            
            # Extract photo URLs using yt-dlp with special handling
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--skip-download',
                '--ignore-no-formats-error',
                '--socket-timeout', '15',
                url
            ]
            
            logger.info(f"Extracting photo URLs: {url}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"Failed to extract photo info: {result.stderr}")
                return None
            
            import json
            try:
                info = json.loads(result.stdout)
            except:
                logger.error("Could not parse JSON output")
                return None
            
            photo_urls = []
            
            # Handle carousel (playlist of photos)
            if info.get('_type') == 'playlist':
                entries = info.get('entries', [])
                logger.info(f"Found carousel with {len(entries)} items")
                for i, entry in enumerate(entries):
                    url_from_entry = entry.get('url') or entry.get('webpage_url')
                    if url_from_entry:
                        photo_urls.append(url_from_entry)
            
            # Handle single photo
            else:
                photo_url = info.get('url')
                if photo_url:
                    photo_urls.append(photo_url)
            
            if not photo_urls:
                logger.error("No photo URLs found")
                return None
            
            # Download each photo using wget or curl (direct download)
            downloaded_files = []
            
            for i, photo_url in enumerate(photo_urls[:10]):  # Limit to 10 photos
                try:
                    # Determine file extension
                    ext = 'jpg'
                    if '.png' in photo_url:
                        ext = 'png'
                    elif '.webp' in photo_url:
                        ext = 'webp'
                    elif '.gif' in photo_url:
                        ext = 'gif'
                    
                    file_path = self.download_dir / f"photo_{timestamp}_{i+1:02d}.{ext}"
                    
                    # Download with curl (more reliable for images)
                    download_cmd = [
                        'curl',
                        '-L',
                        '-o', str(file_path),
                        '--connect-timeout', '10',
                        '--max-time', '30',
                        '-A', 'Mozilla/5.0',
                        photo_url
                    ]
                    
                    result = subprocess.run(download_cmd, capture_output=True, timeout=35)
                    
                    if result.returncode == 0 and file_path.exists():
                        size_mb = os.path.getsize(file_path) / (1024 * 1024)
                        logger.info(f"Downloaded photo {i+1}: {file_path} ({size_mb:.2f} MB)")
                        downloaded_files.append(str(file_path))
                    else:
                        logger.warning(f"Failed to download photo {i+1}")
                
                except Exception as e:
                    logger.error(f"Error downloading photo {i+1}: {e}")
            
            if not downloaded_files:
                logger.error("No photos were successfully downloaded")
                return None
            
            return downloaded_files
        
        except Exception as e:
            logger.error(f"Photo download error: {e}")
            return None
    
    def download(self, url: str, format_type: str = 'mp4') -> Optional[Union[str, list]]:
        """
        Auto-detect and download (video or photo)
        
        Returns:
            - For videos/audio: str (file path)
            - For photos: list (file paths)
            - None if failed
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

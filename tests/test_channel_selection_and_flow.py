import unittest
from unittest.mock import MagicMock, patch
import json
import os
import tempfile

from youtube_search import (
    choose_channel_video,
    choose_least_recent_video,
    generate_stealth_script,
    generate_unique_fingerprint,
)

class TestChannelSelectionAndFlow(unittest.TestCase):
    def setUp(self):
        # Create dummy candidates simulating the channel's /videos tab (newest to oldest)
        self.mock_candidates = [
            {"title": "Video Horrornologi #1 (Paling Baru)", "href": "/watch?v=newest_1", "duration_sec": 1200},
            {"title": "Video Horrornologi #2 (Kedua Baru)", "href": "/watch?v=newest_2", "duration_sec": 1150},
            {"title": "Video Horrornologi #3 (Ketiga Baru)", "href": "/watch?v=newest_3", "duration_sec": 1300},
            {"title": "Video Horrornologi #4 (Katalog Lama A)", "href": "/watch?v=catalog_4", "duration_sec": 1400},
            {"title": "Video Horrornologi #5 (Katalog Lama B)", "href": "/watch?v=catalog_5", "duration_sec": 1250},
            {"title": "Video Horrornologi #6 (Katalog Lama C)", "href": "/watch?v=catalog_6", "duration_sec": 1100},
        ]

    @patch("youtube_search.load_video_stats")
    @patch("youtube_search.get_recent_videos")
    def test_choose_channel_video_latest_priority(self, mock_recents, mock_stats):
        mock_stats.return_value = {}
        mock_recents.return_value = []

        # With latest_ratio=1.0, it must always pick from top 3
        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=1.0)
        self.assertEqual(mode, "LATEST_PRIORITY")
        self.assertIn(chosen["href"], ["/watch?v=newest_1", "/watch?v=newest_2", "/watch?v=newest_3"])

    @patch("youtube_search.load_video_stats")
    @patch("youtube_search.get_recent_videos")
    def test_choose_channel_video_catalog_rotation(self, mock_recents, mock_stats):
        mock_stats.return_value = {}
        mock_recents.return_value = []

        # With latest_ratio=0.0, it must pick from catalog (index >= 3)
        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=0.0)
        self.assertEqual(mode, "CATALOG_ROTATION")
        self.assertIn(chosen["href"], ["/watch?v=catalog_4", "/watch?v=catalog_5", "/watch?v=catalog_6"])

    @patch("youtube_search.load_video_stats")
    @patch("youtube_search.get_recent_videos")
    def test_choose_channel_video_favors_least_watched_latest(self, mock_recents, mock_stats):
        # newest_1 has been watched 10 times, newest_2 has 0 watches
        mock_stats.return_value = {
            "newest_1": {"watch_count": 10},
            "newest_2": {"watch_count": 0},
            "newest_3": {"watch_count": 5},
        }
        mock_recents.return_value = []

        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=1.0)
        self.assertEqual(mode, "LATEST_PRIORITY")
        self.assertEqual(chosen["href"], "/watch?v=newest_2")

    def test_choose_channel_video_empty(self):
        chosen, mode = choose_channel_video([])
        self.assertIsNone(chosen)
        self.assertEqual(mode, "NONE")

    def test_stealth_script_foreground_visibility_spoofing(self):
        fp = generate_unique_fingerprint()
        script = generate_stealth_script(fp)
        
        # Verify document.visibilityState and hidden spoofing for VPS headless execution
        self.assertIn("visibilityState", script)
        self.assertIn("'hidden'", script)
        self.assertIn("hasFocus", script)

if __name__ == "__main__":
    unittest.main()

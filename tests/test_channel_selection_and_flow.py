import unittest
from unittest.mock import MagicMock, patch
import json
import os
import tempfile

from youtube_search import (
    choose_channel_video,
    choose_least_recent_video,
    parse_duration_string,
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

        # With latest_ratio=1.0, it must strictly pick index 0 (the newest release)
        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=1.0)
        self.assertEqual(mode, "LATEST_PRIORITY")
        self.assertEqual(chosen["href"], "/watch?v=newest_1")
        self.assertEqual(chosen["title"], "Video Horrornologi #1 (Paling Baru)")

    @patch("youtube_search.load_video_stats")
    @patch("youtube_search.get_recent_videos")
    def test_choose_channel_video_catalog_rotation(self, mock_recents, mock_stats):
        mock_stats.return_value = {}
        mock_recents.return_value = []

        # With latest_ratio=0.0, it must pick from the rest of the catalog (index >= 1)
        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=0.0)
        self.assertEqual(mode, "CATALOG_ROTATION")
        catalog_hrefs = [c["href"] for c in self.mock_candidates[1:]]
        self.assertIn(chosen["href"], catalog_hrefs)
        self.assertNotEqual(chosen["href"], "/watch?v=newest_1")

    @patch("youtube_search.load_video_stats")
    @patch("youtube_search.get_recent_videos")
    def test_choose_channel_video_strictly_index_zero_regardless_of_history(self, mock_recents, mock_stats):
        # Even if index 0 has high watch count and is in recents,
        # latest priority branch MUST strictly pick index 0 without guessing or picking older ones
        mock_stats.return_value = {
            "newest_1": {"watch_count": 999},
        }
        mock_recents.return_value = [{"id": "newest_1", "title": "Video #1", "time": 123456}]

        chosen, mode = choose_channel_video(self.mock_candidates, latest_ratio=1.0)
        self.assertEqual(mode, "LATEST_PRIORITY")
        self.assertEqual(chosen["href"], "/watch?v=newest_1")
        self.assertEqual(chosen["title"], "Video Horrornologi #1 (Paling Baru)")

    def test_choose_channel_video_single_candidate(self):
        single = [{"title": "Only Video", "href": "/watch?v=single", "duration_sec": 600}]
        chosen_latest, mode_latest = choose_channel_video(single, latest_ratio=1.0)
        self.assertEqual(chosen_latest["href"], "/watch?v=single")
        self.assertEqual(mode_latest, "LATEST_PRIORITY")

        chosen_cat, mode_cat = choose_channel_video(single, latest_ratio=0.0)
        self.assertEqual(chosen_cat["href"], "/watch?v=single")
        self.assertEqual(mode_cat, "CATALOG_ROTATION")

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

    def test_parse_duration_string_formats(self):
        # Format Indonesia (dot: 14.54, 22.35)
        self.assertEqual(parse_duration_string("14.54"), 14 * 60 + 54)
        self.assertEqual(parse_duration_string("22.35"), 22 * 60 + 35)
        # Format standar (colon: 14:54, 1:15:30)
        self.assertEqual(parse_duration_string("14:54"), 14 * 60 + 54)
        self.assertEqual(parse_duration_string("1:15:30"), 3600 + 15 * 60 + 30)
        self.assertEqual(parse_duration_string("1.15.30"), 3600 + 15 * 60 + 30)
        # String kosong / invalid
        self.assertEqual(parse_duration_string(""), 0)
        self.assertEqual(parse_duration_string("invalid"), 0)

if __name__ == "__main__":
    unittest.main()

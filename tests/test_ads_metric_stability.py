import unittest
from web_dashboard import DashboardStateManager, create_app
from bot_controller import BotConfig

class TestAdsMetricStability(unittest.TestCase):
    def setUp(self):
        self.state = DashboardStateManager(initial_config={
            "batch_count": 50,
            "workers": 2,
            "keyword": "horrornologi",
            "channel": "horrornologi"
        })

    def test_ads_count_strictly_monotonic_across_sessions(self):
        """
        Simulate the exact scenario reported by the user:
        Worker 1 and Worker 2 skip ads reaching 8 total ads.
        Then Worker 1 finishes its video and starts a new video (ad_count in session resets to 0 or 1).
        Verify that total ads DOES NOT DROP from 8 to 6!
        """
        # Step 1: Worker 1 skips 3 ads
        for _ in range(3):
            self.state.process_event({
                "worker_id": 1,
                "event": "ad_skipped",
                "ad_count": 1,
                "ad_type": "skippable"
            })

        # Step 2: Worker 2 skips 5 ads
        for _ in range(5):
            self.state.process_event({
                "worker_id": 2,
                "event": "ad_skipped",
                "ad_count": 1,
                "ad_type": "skippable"
            })

        snap = self.state.get_snapshot()
        self.assertEqual(snap["summary"]["total_ads_skipped"], 8)
        self.assertEqual(snap["workers"][1]["ads_skipped"], 3)
        self.assertEqual(snap["workers"][2]["ads_skipped"], 5)

        # Step 3: Worker 1 finishes video 1
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_complete",
            "status": "SUKSES",
            "details": "Target selesai"
        })
        self.state.process_event({
            "worker_id": 1,
            "event": "session_success",
            "total_success": 1
        })

        # Total ads must still be 8
        snap2 = self.state.get_snapshot()
        self.assertEqual(snap2["summary"]["total_ads_skipped"], 8)

        # Step 4: Worker 1 starts video 2 (new video session starts)
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_start",
            "title": "Video Horrornologi Baru 2",
            "target_duration": 120.0
        })

        # Worker 1 sends watch_progress with ad_count=0 or 1 for this new session
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_progress",
            "elapsed": 10.0,
            "duration": 120.0,
            "percent": 8.3,
            "ad_count": 0,
            "skippable_ads": 0,
            "nonskippable_ads": 0
        })

        # CRITICAL TEST: Total ads must remain stable at 8! (NOT 0 + 5 = 5 or 6)
        snap3 = self.state.get_snapshot()
        self.assertEqual(snap3["summary"]["total_ads_skipped"], 8)
        self.assertEqual(snap3["workers"][1]["ads_skipped"], 3)
        self.assertEqual(snap3["workers"][2]["ads_skipped"], 5)

        # Worker 1 skips 1 ad in video 2
        self.state.process_event({
            "worker_id": 1,
            "event": "ad_skipped",
            "ad_count": 1,
            "ad_type": "skippable"
        })

        # Total ads must increment to 9 (8 + 1)
        snap4 = self.state.get_snapshot()
        self.assertEqual(snap4["summary"]["total_ads_skipped"], 9)
        self.assertEqual(snap4["workers"][1]["ads_skipped"], 4)

    def test_total_views_stability(self):
        """Verify that total_views is monotonic and never drops."""
        self.state.process_event({
            "worker_id": 1,
            "event": "session_success",
            "total_success": 5
        })
        snap1 = self.state.get_snapshot()
        self.assertEqual(snap1["summary"]["total_views"], 5)

        # Event with lower or missing total_success should not decrement
        self.state.process_event({
            "worker_id": 2,
            "event": "watch_complete",
            "details": "Done"
        })
        snap2 = self.state.get_snapshot()
        self.assertGreaterEqual(snap2["summary"]["total_views"], 5)

    def test_bot_controller_config_parity(self):
        """Verify BotConfig default values and dictionary conversions."""
        cfg = BotConfig()
        self.assertEqual(cfg.workers, 3)
        self.assertEqual(cfg.batch_count, 50)
        self.assertEqual(cfg.keyword, "horrornologi")
        self.assertEqual(cfg.channel, "horrornologi")
        self.assertEqual(cfg.headless, True)
        self.assertEqual(cfg.headed, False)

        # Override test
        data = {"workers": 5, "headed": True, "keyword": "misteri"}
        cfg_custom = BotConfig.from_dict(data)
        self.assertEqual(cfg_custom.workers, 5)
        self.assertEqual(cfg_custom.headed, True)
        self.assertEqual(cfg_custom.headless, False)
        self.assertEqual(cfg_custom.keyword, "misteri")

if __name__ == "__main__":
    unittest.main()

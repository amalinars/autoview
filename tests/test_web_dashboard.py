import unittest
import json
from starlette.testclient import TestClient

class TestWebDashboard(unittest.TestCase):
    def setUp(self):
        from web_dashboard import DashboardStateManager, create_app
        self.state = DashboardStateManager(initial_config={"batch_count": 50, "workers": 2, "keyword": "test"})
        self.app = create_app(self.state)
        self.client = TestClient(self.app)

    def test_initial_snapshot(self):
        snapshot = self.state.get_snapshot()
        self.assertIn("summary", snapshot)
        self.assertIn("workers", snapshot)
        self.assertEqual(snapshot["summary"]["batch_target"], 50)
        self.assertEqual(snapshot["summary"]["total_views"], 0)

    def test_process_worker_lifecycle_events(self):
        # 1. Init event
        self.state.process_event({
            "worker_id": 1,
            "event": "init",
            "pid": 12345,
            "status": "SIAP",
            "details": "PID: 12345"
        })
        snap = self.state.get_snapshot()
        self.assertIn(1, snap["workers"])
        self.assertEqual(snap["workers"][1]["pid"], 12345)
        self.assertEqual(snap["workers"][1]["status"], "SIAP")

        # 2. Proxy selected
        self.state.process_event({
            "worker_id": 1,
            "event": "proxy_selected",
            "proxy": "http://1.2.3.4:8080",
            "details": "Testing proxy"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["proxy"], "http://1.2.3.4:8080")

        # 3. Watch progress
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_progress",
            "elapsed": 45.0,
            "duration": 100.0,
            "percent": 45.0,
            "details": "00:45 (45.0%)"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["elapsed_sec"], 45.0)
        self.assertEqual(snap["workers"][1]["duration_sec"], 100.0)
        self.assertEqual(snap["workers"][1]["percent"], 45.0)

        # 4. Watch complete
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_complete",
            "status": "SUKSES",
            "details": "Target selesai"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["status"], "SUKSES")
        self.assertEqual(snap["summary"]["total_views"], 1)

    def test_api_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("workers", data)

    def test_api_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

if __name__ == "__main__":
    unittest.main()

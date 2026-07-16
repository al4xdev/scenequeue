import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import httpx

# Ensure correct pathing
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server import app


class SyncASGIClient:
    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


class TestAPI(unittest.TestCase):
    def setUp(self):
        self.client = SyncASGIClient()
        self.db_name_patcher = patch("routes.api.get_active_db_name", return_value="default")
        self.db_name_patcher.start()

    def tearDown(self):
        self.db_name_patcher.stop()

    @patch("routes.api.cfg.reload_config")
    @patch("routes.api.cfg.save_config")
    def test_update_config_from_frontend(self, mock_save_config, mock_reload_config):
        payload = {
            "comfy_url": "http://127.0.0.1:8188",
            "target_node_id": "2",
            "target_input_key": "text",
            "width": 1024,
            "height": 768,
            "comfy_root": "/opt/ComfyUI",
            "checkpoint": "example.safetensors",
            "loras": [],
            "chunk_size": 4,
        }

        response = self.client.post("/api/config", json=payload)

        self.assertEqual(response.status_code, 200)
        mock_save_config.assert_called_once_with(payload)
        mock_reload_config.assert_called_once()

    def test_update_config_rejects_invalid_generation_values(self):
        response = self.client.post(
            "/api/config",
            json={
                "comfy_url": "http://127.0.0.1:8188",
                "target_node_id": "2",
                "target_input_key": "text",
                "width": 0,
                "height": 1024,
                "comfy_root": "",
                "chunk_size": 0,
            },
        )

        self.assertEqual(response.status_code, 422)

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("routes.api.ComfyClient.queue_prompt")
    @patch("routes.api.build_batch")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_edit_prompt_success(
        self,
        mock_unlink,
        mock_exists,
        mock_build_batch,
        mock_queue_prompt,
        mock_save_db,
        mock_load_db,
        mock_save_state,
        mock_load_state,
    ):
        # Setup mock data for load_state
        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 2,
                "prompt_resolved": "old resolved",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id",
                "status": "completed",
                "filename": "gallery_data/images/item_123.png",
                "image_index": 2,
                "config": {
                    "subject": "PERSON",
                    "appearance": "CASUAL",
                    "wardrobe": "CASUAL",
                    "pose": "PORTRAIT",
                    "scene": "STUDIO",
                },
            }
        ]

        # Setup mock db
        mock_load_db.return_value = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
                {"index": 2, "text": "seg 2", "history": []},
            ],
        }

        mock_queue_prompt.return_value = "new_prompt_id"
        mock_build_batch.return_value = {"mock_node": "mock_val"}
        mock_exists.return_value = True

        # Call endpoint
        response = self.client.post(
            "/api/edit-prompt", json={"item_id": "item_123", "prompt": "new prompt text"}
        )

        # Asserts
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["id"], "item_123")
        self.assertEqual(json_data["status"], "pending")
        self.assertIn("new prompt text", json_data["prompt_resolved"])

        # Check queue prompt called with build_batch workflow
        mock_queue_prompt.assert_called_once_with({"mock_node": "mock_val"})

        # Verify db was updated and saved
        mock_save_db.assert_called_once()
        mock_save_state.assert_called_once()

    @patch("routes.api.load_state")
    def test_edit_prompt_not_found(self, mock_load_state):
        mock_load_state.return_value = []
        response = self.client.post(
            "/api/edit-prompt", json={"item_id": "invalid_id", "prompt": "new prompt text"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Item not found")

    @patch("routes.api.load_state")
    def test_edit_prompt_empty(self, mock_load_state):
        mock_load_state.return_value = [{"id": "item_123"}]
        response = self.client.post(
            "/api/edit-prompt", json={"item_id": "item_123", "prompt": "  "}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Prompt cannot be empty")

    @patch("routes.api.cfg")
    @patch("httpx.AsyncClient.post")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_ai_preview_prompt_success(self, mock_read_text, mock_exists, mock_post, mock_cfg):
        from pathlib import Path

        mock_cfg.OPENROUTER_API_KEY = "test_key"
        mock_cfg.OPENROUTER_MODELS = ["model_a"]
        mock_cfg.ROOT = Path(__file__).resolve().parent.parent
        mock_exists.return_value = True
        mock_read_text.return_value = "guidelines contents"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "adult person, warm expression, studio"}}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/ai/preview-prompt",
            json={
                "original_prompt": "adult person, neutral expression, studio",
                "instruction": "increase blush to 1.1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["new_prompt"], "adult person, warm expression, studio")

    @patch("routes.api.cfg")
    @patch("httpx.AsyncClient.post")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_ai_preview_prompt_follow_up(self, mock_read_text, mock_exists, mock_post, mock_cfg):
        from pathlib import Path

        mock_cfg.OPENROUTER_API_KEY = "test_key"
        mock_cfg.OPENROUTER_MODELS = ["model_a"]
        mock_cfg.ROOT = Path(__file__).resolve().parent.parent
        mock_exists.return_value = True
        mock_read_text.return_value = "guidelines"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "adult person, subtle warm expression, studio"}}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/ai/preview-prompt",
            json={
                "original_prompt": "adult person, neutral expression, studio",
                "instruction": "no, make blush weight 0.8",
                "ai_suggestion": "adult person, warm expression, studio",
                "previous_instruction": "increase blush to 1.1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["new_prompt"], "adult person, subtle warm expression, studio"
        )

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    def test_add_segment_append(self, mock_save_db, mock_load_db, mock_save_state, mock_load_state):
        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "db_name": "default",
                "segment_index": 0,
                "config": {},
            }
        ]
        mock_load_db.return_value = {
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
            ]
        }
        response = self.client.post("/api/prompts/add", json={"text": "new segment"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["index"], 2)
        self.assertEqual(data["total"], 3)
        mock_save_db.assert_called_once()
        saved_db = mock_save_db.call_args[0][0]
        self.assertEqual(len(saved_db["segments"]), 3)
        self.assertEqual(saved_db["segments"][2]["text"], "new segment")
        self.assertEqual(saved_db["segments"][2]["index"], 2)
        mock_save_state.assert_called_once()

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    def test_add_segment_insert_at(
        self, mock_save_db, mock_load_db, mock_save_state, mock_load_state
    ):
        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "db_name": "default",
                "segment_index": 0,
                "config": {},
            }
        ]
        mock_load_db.return_value = {
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
            ]
        }
        response = self.client.post(
            "/api/prompts/add", json={"text": "inserted segment", "insert_at": 1}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["index"], 1)
        self.assertEqual(data["total"], 3)
        mock_save_db.assert_called_once()
        saved_db = mock_save_db.call_args[0][0]
        self.assertEqual(len(saved_db["segments"]), 3)
        self.assertEqual(saved_db["segments"][0]["text"], "seg 0")
        self.assertEqual(saved_db["segments"][1]["text"], "inserted segment")
        self.assertEqual(saved_db["segments"][2]["text"], "seg 1")
        self.assertEqual(saved_db["segments"][0]["index"], 0)
        self.assertEqual(saved_db["segments"][1]["index"], 1)
        self.assertEqual(saved_db["segments"][2]["index"], 2)
        mock_save_state.assert_called_once()

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("routes.api.ComfyClient.queue_prompt")
    @patch("routes.api.build_batch")
    def test_insert_segment_job_before(
        self,
        mock_build_batch,
        mock_queue_prompt,
        mock_save_db,
        mock_load_db,
        mock_save_state,
        mock_load_state,
    ):
        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 1,
                "prompt_resolved": "old resolved",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id",
                "status": "completed",
                "filename": "gallery_data/images/item_123.png",
                "image_index": 0,
                "config": {},
            }
        ]
        mock_load_db.return_value = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
            ],
        }
        mock_queue_prompt.return_value = "new_prompt_id"
        mock_build_batch.return_value = {}

        response = self.client.post(
            "/api/insert-segment-job", json={"item_id": "item_123", "position": "before"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["insert_at"], 1)

        mock_save_db.assert_called_once()
        mock_save_state.assert_called_once()

    @patch("routes.api.cfg")
    @patch("httpx.AsyncClient.post")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("routes.api.resolve_enum_value")
    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("routes.api.ComfyClient.queue_prompt")
    @patch("routes.api.build_batch")
    def test_insert_segment_job_ai(
        self,
        mock_build_batch,
        mock_queue_prompt,
        mock_save_db,
        mock_load_db,
        mock_save_state,
        mock_load_state,
        mock_resolve,
        mock_read_text,
        mock_exists,
        mock_post,
        mock_cfg,
    ):
        from pathlib import Path

        mock_cfg.OPENROUTER_API_KEY = "test_key"
        mock_cfg.OPENROUTER_MODELS = ["model_a"]
        mock_cfg.ROOT = Path(__file__).resolve().parent.parent
        mock_exists.return_value = True
        mock_read_text.return_value = "guidelines"
        mock_resolve.return_value = "PERSON"

        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 1,
                "prompt_resolved": "old resolved",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id",
                "status": "completed",
                "filename": "gallery_data/images/item_123.png",
                "image_index": 0,
                "config": {},
            }
        ]
        mock_load_db.return_value = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
            ],
        }
        mock_queue_prompt.return_value = "new_prompt_id"
        mock_build_batch.return_value = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "adult person, stronger warm expression, studio"}}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/insert-segment-job",
            json={
                "item_id": "item_123",
                "position": "before",
                "use_ai": True,
                "instruction": "make her blush",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["insert_at"], 1)

        saved_db = mock_save_db.call_args[0][0]
        self.assertEqual(
            saved_db["segments"][1]["text"], "adult person, stronger warm expression, studio"
        )

    @patch("routes.api.cfg")
    @patch("httpx.AsyncClient.post")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("routes.api.resolve_enum_value")
    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("routes.api.ComfyClient.queue_prompt")
    @patch("routes.api.build_batch")
    def test_insert_segment_job_ai_multi(
        self,
        mock_build_batch,
        mock_queue_prompt,
        mock_save_db,
        mock_load_db,
        mock_save_state,
        mock_load_state,
        mock_resolve,
        mock_read_text,
        mock_exists,
        mock_post,
        mock_cfg,
    ):
        from pathlib import Path

        mock_cfg.OPENROUTER_API_KEY = "test_key"
        mock_cfg.OPENROUTER_MODELS = ["model_a"]
        mock_cfg.ROOT = Path(__file__).resolve().parent.parent
        mock_exists.return_value = True
        mock_read_text.return_value = "guidelines"
        mock_resolve.return_value = "PERSON"

        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 1,
                "prompt_resolved": "old resolved",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id",
                "status": "completed",
                "filename": "gallery_data/images/item_123.png",
                "image_index": 0,
                "config": {},
            }
        ]
        db_mock = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
            ],
        }
        mock_load_db.return_value = db_mock
        mock_queue_prompt.return_value = "new_prompt_id"
        mock_build_batch.return_value = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "adult person, stronger warm expression, studio"}}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/insert-segment-job",
            json={
                "item_id": "item_123",
                "position": "before",
                "use_ai": True,
                "instruction": "make her blush",
                "count": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["insert_at"], 2)

        self.assertEqual(len(db_mock["segments"]), 4)

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("pathlib.Path.unlink")
    def test_delete_segment_job_success(
        self, mock_unlink, mock_save_db, mock_load_db, mock_save_state, mock_load_state
    ):
        mock_load_state.return_value = [
            {
                "id": "item_123",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 1,
                "prompt_resolved": "old resolved",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id",
                "status": "completed",
                "filename": "gallery_data/images/item_123.png",
                "image_index": 0,
                "config": {},
            },
            {
                "id": "item_124",
                "session_id": "session_abc",
                "parent_id": None,
                "db_name": "default",
                "segment_index": 2,
                "prompt_resolved": "old resolved 2",
                "chunk_number": "0",
                "prompt_id": "prev_prompt_id_2",
                "status": "completed",
                "filename": "gallery_data/images/item_124.png",
                "image_index": 1,
                "config": {},
            },
        ]
        mock_load_db.return_value = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
                {"index": 2, "text": "seg 2", "history": []},
            ],
        }

        response = self.client.post("/api/delete-segment-job", json={"item_id": "item_123"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["deleted_index"], 1)

        # check that mock_save_db was called and index updated
        mock_save_db.assert_called_once()
        saved_db = mock_save_db.call_args[0][0]
        # index 1 (seg 1) should be popped, seg 2 should now have index 1
        self.assertEqual(len(saved_db["segments"]), 2)
        self.assertEqual(saved_db["segments"][1]["text"], "seg 2")
        self.assertEqual(saved_db["segments"][1]["index"], 1)

        # check state was updated and saved
        mock_save_state.assert_called_once()
        saved_st = mock_save_state.call_args[0][0]
        # item_123 should be deleted, item_124 segment_index should decrement to 1
        self.assertEqual(len(saved_st), 1)
        self.assertEqual(saved_st[0]["id"], "item_124")
        self.assertEqual(saved_st[0]["segment_index"], 1)

        # verify mock_unlink was called for images
        self.assertTrue(mock_unlink.called)

    @patch("routes.api.get_all_segments")
    def test_get_autocomplete_tags_success(self, mock_get_all_segments):
        mock_get_all_segments.return_value = [
            {"text": "crying, (blushing:1.2), POVDress"},
            {"text": "indoor, (blushing:0.95), [bed]"},
        ]
        response = self.client.get("/api/autocomplete-tags")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, ["bed", "blushing", "crying", "indoor", "POVDress"])

    @patch("routes.api.get_db_lookup")
    def test_resolve_enum_value_db_name_isolation(self, mock_get_db_lookup):
        """resolve_enum_value must use session db_name when provided, not the global active DB."""
        from routes.api import resolve_enum_value
        from src.enums import Subject

        # db_name=None → uses globally active DB ("default" mocked in setUp)
        mock_get_db_lookup.return_value = {"PERSON": "adult person"}
        result = resolve_enum_value("subjects", "PERSON", Subject)
        mock_get_db_lookup.assert_called_with("subjects", "default")
        self.assertEqual(result, "adult person")

        # db_name="custom_db" → uses custom_db directly, bypasses active DB
        mock_get_db_lookup.reset_mock()
        mock_get_db_lookup.return_value = {"PERSON": "resolved_from_custom"}
        result = resolve_enum_value("subjects", "PERSON", Subject, db_name="custom_db")
        mock_get_db_lookup.assert_called_with("subjects", "custom_db")
        self.assertEqual(result, "resolved_from_custom")

    @patch("routes.api.load_state")
    @patch("routes.api.save_state")
    @patch("routes.api.load_db")
    @patch("routes.api.save_db")
    @patch("routes.api.ComfyClient.queue_prompt")
    @patch("routes.api.build_batch")
    @patch("routes.api.get_session")
    @patch("routes.api.resolve_enum_value")
    def test_insert_segment_job_cross_db_resolution(
        self,
        mock_resolve_enum,
        mock_get_session,
        mock_build_batch,
        mock_queue_prompt,
        mock_save_db,
        mock_load_db,
        mock_save_state,
        mock_load_state,
    ):
        """Placeholders for other sessions must resolve enums against THEIR db_name, not the source's."""
        # Both items share the same prompts DB name so they end up in the same
        # target_sessions group.  Their session records in sessions.json carry
        # different db_name values — the source session uses the shared DB,
        # while the other session was created against a different enum DB.
        mock_load_state.return_value = [
            {
                "id": "item_A1",
                "session_id": "session_A",
                "parent_id": None,
                "db_name": "shared_db",
                "segment_index": 1,
                "prompt_resolved": "old A",
                "chunk_number": "0",
                "prompt_id": "prev_A",
                "status": "completed",
                "filename": "img_A.png",
                "image_index": 0,
                "config": {"subject": "PERSON", "appearance": "CASUAL"},
            },
            {
                "id": "item_B1",
                "session_id": "session_B",
                "parent_id": None,
                "db_name": "shared_db",
                "segment_index": 2,
                "prompt_resolved": "old B",
                "chunk_number": "0",
                "prompt_id": "prev_B",
                "status": "completed",
                "filename": "img_B.png",
                "image_index": 0,
                "config": {"subject": "CUSTOM", "appearance": "CURVY"},
            },
        ]

        # Session B's record carries a different db_name for enum resolution
        mock_get_session.return_value = {
            "id": "session_B",
            "db_name": "other_db",
            "subject_config": {"subject": "CUSTOM", "appearance": "CURVY"},
        }

        mock_load_db.return_value = {
            "version": 2,
            "segments": [
                {"index": 0, "text": "seg 0", "history": []},
                {"index": 1, "text": "seg 1", "history": []},
                {"index": 2, "text": "seg 2", "history": []},
            ],
        }

        mock_queue_prompt.return_value = "new_prompt_id"
        mock_build_batch.return_value = {}
        mock_resolve_enum.return_value = "resolved_value"

        # Insert before item_A1 (source session uses db_name="shared_db")
        response = self.client.post(
            "/api/insert-segment-job",
            json={"item_id": "item_A1", "position": "before"},
        )

        self.assertEqual(response.status_code, 200)

        # At least one resolve_enum_value call must have db_name="other_db"
        other_db_calls = [
            c for c in mock_resolve_enum.call_args_list if len(c[0]) >= 4 and c[0][3] == "other_db"
        ]
        self.assertGreater(
            len(other_db_calls),
            0,
            "resolve_enum_value should be called with db_name='other_db' "
            "for session B's placeholder",
        )

        # At least one call must have db_name="shared_db" (source session)
        shared_calls = [
            c for c in mock_resolve_enum.call_args_list if len(c[0]) >= 4 and c[0][3] == "shared_db"
        ]
        self.assertGreater(
            len(shared_calls),
            0,
            "resolve_enum_value should be called with db_name='shared_db' for the source session",
        )


if __name__ == "__main__":
    unittest.main()

"""Focused offline tests for the P5.9P disposable acceptance harness."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import p5_9p_fake_provider as fake_provider
import run_p5_9p_personal_acceptance as acceptance


class _MissingTerminalSSEHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        body = (
            b'event: meta\ndata: {"task_id":"00000000-0000-4000-8000-000000000010"}\n\n'
            b'event: chunk\ndata: {"content":"partial"}\n\n'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class P59PAcceptanceHarnessTests(unittest.TestCase):
    def test_canonical_json_is_sorted_and_newline_terminated(self) -> None:
        self.assertEqual(acceptance._canonical({"z": 1, "a": 2}), b'{"a":2,"z":1}\n')

    def test_operator_env_uses_the_closed_key_shape(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "operator.env"
            acceptance._write_operator_env(
                path,
                port=39123,
                database="omnibase_test_p59_unit",
                database_user="omnibase_p59_unit",
                deployment_id="00000000-0000-4000-8000-000000000001",
                password="synthetic-postgres-value",  # noqa: S106
                redis_password="synthetic-redis-value",  # noqa: S106
                minio_password="synthetic-minio-value",  # noqa: S106
                jwt_secret="synthetic-jwt-value",  # noqa: S106
                provider_key="synthetic-provider-value",
                memory_key="synthetic-memory-value",
            )
            keys = {
                line.split("=", 1)[0]
                for line in path.read_text(encoding="utf-8").splitlines()
            }
        self.assertEqual(
            keys,
            {
                "CORS_ORIGINS",
                "DATABASE_URL",
                "JWT_SECRET",
                "MEMORY_CONTENT_ENCRYPTION_KEY",
                "MINIO_BUCKET",
                "MINIO_ROOT_PASSWORD",
                "MINIO_ROOT_USER",
                "OMNIBASE_DEPLOYMENT_INSTANCE_ID",
                "OMNIBASE_FRONTEND_PORT",
                "POSTGRES_DB",
                "POSTGRES_PASSWORD",
                "POSTGRES_USER",
                "PROVIDER_CREDENTIAL_ENCRYPTION_KEY",
                "PROVIDER_ENDPOINT_ALLOWLIST",
                "REDIS_PASSWORD",
                "REDIS_URL",
            },
        )

    def test_target_database_names_are_disposable_and_restore_new(self) -> None:
        with TemporaryDirectory() as repo_dir, TemporaryDirectory() as work_dir:
            journey = acceptance.Journey(
                repo=Path(repo_dir),
                work_root=Path(work_dir),
                lease_wait_seconds=95,
            )
            source = journey._create_target(suffix="a1b2c3d4")
            restored = journey._create_target(suffix="a1b2c3d4", restore=True)
        self.assertEqual(source.database, "omnibase_test_p59_a1b2c3d4")
        self.assertEqual(restored.database, "omnibase_restore_p59_a1b2c3d4")
        self.assertNotEqual(source.project, restored.project)

    def test_lease_wait_below_the_real_ttl_is_rejected(self) -> None:
        with TemporaryDirectory() as repo_dir:
            with self.assertRaisesRegex(SystemExit, "must exceed"):
                acceptance.main(
                    [
                        "--repo-root",
                        repo_dir,
                        "--lease-wait-seconds",
                        "90",
                    ]
                )

    def test_receipt_rejects_secret_keys_and_secret_locators(self) -> None:
        acceptance._assert_receipt_safe(
            {
                "acceptance": "passed",
                "database": "omnibase_restore_p59_unit",
                "runtime_enabled": False,
            }
        )
        with self.assertRaisesRegex(acceptance.AcceptanceError, "sensitive key"):
            acceptance._assert_receipt_safe({"access_token": "synthetic"})
        with self.assertRaisesRegex(acceptance.AcceptanceError, "secret locator"):
            acceptance._assert_receipt_safe({"error": "redis://example.invalid/0"})

    def test_error_output_redacts_run_scoped_secrets(self) -> None:
        with TemporaryDirectory() as repo_dir, TemporaryDirectory() as work_dir:
            journey = acceptance.Journey(
                repo=Path(repo_dir),
                work_root=Path(work_dir),
                lease_wait_seconds=95,
            )
            journey.redaction_values.add("synthetic-private-value")
            redacted = journey.redact_error(
                "provider failed with synthetic-private-value in diagnostic output"
            )
        self.assertEqual(
            redacted,
            "provider failed with [REDACTED] in diagnostic output",
        )

    def test_compose_overlay_never_opens_runtime_or_provider_host_port(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        overlay = (
            repo / "deployment/personal-production/acceptance.compose.yml"
        ).read_text(encoding="utf-8")
        base = (repo / "deployment/personal-production/compose.yml").read_text(
            encoding="utf-8"
        )
        fake_provider_section = overlay.split("  backend:", 1)[0]
        self.assertNotIn("ports:", fake_provider_section)
        self.assertIn('restart: "no"', overlay.split("  backend:", 1)[1])
        self.assertNotIn("AGENT_RUNTIME_ENABLED", overlay)
        self.assertNotIn("AGENT_PLANNER_ENABLED", overlay)
        self.assertNotIn("MULTI_AGENT_ENABLED", overlay)
        self.assertIn('AGENT_RUNTIME_ENABLED: "false"', base)
        self.assertIn('AGENT_PLANNER_ENABLED: "false"', base)
        self.assertIn('MULTI_AGENT_ENABLED: "false"', base)
        self.assertIn("P5_ACCEPTANCE_FIXTURE_PATH", overlay)

    def test_fake_provider_stream_and_stats_are_bounded(self) -> None:
        with fake_provider._LOCK:
            fake_provider._STATS.update(
                call_count=0,
                saw_memory_marker=False,
                saw_skill_marker=False,
            )
        server = ThreadingHTTPServer(("127.0.0.1", 0), fake_provider._Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", int(server.server_address[1]), timeout=10
            )
            payload = json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "P5_MEMORY_MARKER P5_SKILL_MARKER private-prompt",
                        }
                    ]
                }
            )
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=payload,
                headers={
                    "Authorization": "Bearer unit-test",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            body = response.read().decode()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertIn('"finish_reason":"stop"', body)
        self.assertIn('"usage":', body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))
        with fake_provider._LOCK:
            stats = dict(fake_provider._STATS)
        self.assertEqual(
            stats,
            {
                "call_count": 1,
                "saw_memory_marker": True,
                "saw_skill_marker": True,
            },
        )
        serialized = json.dumps(stats)
        self.assertNotIn("private-prompt", serialized)
        self.assertNotIn("unit-test", serialized)

    def test_product_sse_requires_a_terminal_event(self) -> None:
        with TemporaryDirectory() as repo_dir, TemporaryDirectory() as work_dir:
            journey = acceptance.Journey(
                repo=Path(repo_dir),
                work_root=Path(work_dir),
                lease_wait_seconds=95,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _MissingTerminalSSEHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            coordinates = acceptance.ProductCoordinates(
                access_token="synthetic-access",
                email="owner@example.invalid",
                password="synthetic-password",  # noqa: S106
                tenant_id="00000000-0000-4000-8000-000000000001",
                owner_user_id="00000000-0000-4000-8000-000000000002",
                workspace_id="00000000-0000-4000-8000-000000000003",
                agent_version_id="00000000-0000-4000-8000-000000000004",
            )
            target = acceptance.Target(
                project="unit",
                env_file=Path(work_dir) / "unused.env",
                port=int(server.server_address[1]),
                database="omnibase_test_p59_unit",
                database_user="omnibase_p59_unit",
            )
            try:
                with self.assertRaisesRegex(
                    acceptance.AcceptanceError, "terminal event"
                ):
                    journey._stream(
                        target,
                        coordinates,
                        message="bounded",
                        idempotency_key="unit-idempotency-key",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

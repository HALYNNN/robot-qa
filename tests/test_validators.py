"""검증 스크립트 단위 테스트.

pytest와 unittest 어느 쪽으로도 실행된다.

    pytest -q
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_label_dataset as labels  # noqa: E402
import validate_nav2_log as nav2  # noqa: E402


def codes(result):
    return [finding["code"] for finding in result["findings"]]


def severity_of(result, code):
    return [finding["severity"] for finding in result["findings"] if finding["code"] == code]


def run_cli(script_name, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class TempJsonlMixin(unittest.TestCase):
    def validate_lines(self, records, config=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )
            return nav2.run(path, config)


class Nav2LogTests(TempJsonlMixin):
    def test_clean_log_passes(self):
        records = [
            {"timestamp": 0.0, "event": "run_start"},
            {"timestamp": 0.5, "event": "topic", "topic": "/scan", "expected_hz": 5.0, "actual_hz": 5.0, "delay_ms": 30, "dropped": 0},
            {"timestamp": 1.0, "event": "goal", "goal_id": "g1", "status": "ACCEPTED"},
            {"timestamp": 20.0, "event": "goal", "goal_id": "g1", "status": "SUCCEEDED"},
            {"timestamp": 21.0, "event": "run_end"},
        ]
        result = self.validate_lines(records)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["summary"]["verdict"], "PASS")
        self.assertEqual(result["records"], 5)

    def test_timestamp_inversion_detected(self):
        records = [
            {"timestamp": 10.0, "event": "goal", "goal_id": "g1", "status": "ACCEPTED"},
            {"timestamp": 9.5, "event": "log", "level": "INFO", "message": "late write"},
            {"timestamp": 12.0, "event": "run_end"},
        ]
        result = self.validate_lines(records)
        self.assertEqual(codes(result), ["TIMESTAMP_INVERSION"])
        self.assertEqual(result["summary"]["verdict"], "FAIL")
        self.assertAlmostEqual(result["findings"][0]["details"]["previous_timestamp"], 10.0)

    def test_timestamp_tolerance_allows_jitter(self):
        records = [
            {"timestamp": 10.0, "event": "log"},
            {"timestamp": 9.99, "event": "log"},
        ]
        result = self.validate_lines(records, nav2.Config(timestamp_tolerance_s=0.05))
        self.assertEqual(result["findings"], [])

    def test_goal_failure_detected_for_aborted_and_failed(self):
        records = [
            {"timestamp": 1.0, "event": "goal", "goal_id": "g1", "status": "ABORTED", "reason": "Failed to create plan"},
            {"timestamp": 2.0, "event": "goal_result", "goal_id": "g2", "status": "FAILED"},
            {"timestamp": 3.0, "event": "goal", "goal_id": "g3", "status": "SUCCEEDED"},
        ]
        result = self.validate_lines(records)
        self.assertEqual(codes(result), ["GOAL_FAILURE", "GOAL_FAILURE"])
        self.assertEqual(result["summary"]["goals"], {"ABORTED": 1, "FAILED": 1, "SUCCEEDED": 1})
        self.assertIn("g1", result["findings"][0]["message"])

    def test_topic_delay_and_drop_detected(self):
        records = [
            {"timestamp": 1.0, "event": "topic", "topic": "/scan", "expected_hz": 5.0, "actual_hz": 3.4, "delay_ms": 470, "dropped": 6},
        ]
        result = self.validate_lines(records)
        self.assertEqual(sorted(codes(result)), ["TOPIC_DELAY", "TOPIC_DROP"])
        self.assertEqual(severity_of(result, "TOPIC_DELAY"), ["warning"])
        self.assertEqual(severity_of(result, "TOPIC_DROP"), ["error"])
        self.assertEqual(result["summary"]["topics"]["/scan"]["dropped"], 6)

    def test_hard_delay_is_error(self):
        records = [
            {"timestamp": 1.0, "event": "topic", "topic": "/odom", "expected_hz": 20.0, "actual_hz": 20.0, "delay_ms": 1500},
        ]
        result = self.validate_lines(records)
        self.assertEqual(severity_of(result, "TOPIC_DELAY"), ["error"])

    def test_emergency_stop_is_warning(self):
        records = [
            {"timestamp": 1.0, "event": "emergency_stop", "source": "collision_monitor", "reason": "obstacle"},
            {"timestamp": 2.0, "event": "emergency_stop", "source": "collision_monitor", "active": False},
        ]
        result = self.validate_lines(records)
        self.assertEqual(codes(result), ["EMERGENCY_STOP"])
        self.assertEqual(result["summary"]["emergency_stops"], 1)
        self.assertEqual(result["summary"]["verdict"], "PASS")

    def test_fail_on_warning_option(self):
        records = [{"timestamp": 1.0, "event": "emergency_stop", "source": "collision_monitor"}]
        result = self.validate_lines(records, nav2.Config(fail_on_warning=True))
        self.assertEqual(result["summary"]["verdict"], "FAIL")

    def test_invalid_json_line_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            path.write_text(
                '{"timestamp": 1.0, "event": "run_start"}\n{not json}\n\n{"timestamp": 2.0, "event": "run_end"}\n',
                encoding="utf-8",
            )
            result = nav2.run(path)
        self.assertEqual(codes(result), ["INVALID_JSON"])
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["summary"]["verdict"], "FAIL")

    def test_sample_log_expected_findings(self):
        result = nav2.run(ROOT / "samples" / "sample_nav2_log.jsonl")
        self.assertEqual(
            sorted(result["summary"]["by_code"].items()),
            sorted(
                [
                    ("EMERGENCY_STOP", 1),
                    ("GOAL_FAILURE", 1),
                    ("TIMESTAMP_INVERSION", 1),
                    ("TOPIC_DELAY", 1),
                    ("TOPIC_DROP", 1),
                ]
            ),
        )
        self.assertEqual(result["summary"]["errors"], 3)
        self.assertEqual(result["summary"]["warnings"], 2)
        self.assertEqual(result["summary"]["verdict"], "FAIL")

    def test_cli_json_output_and_exit_code(self):
        completed = run_cli("validate_nav2_log.py", str(ROOT / "samples" / "sample_nav2_log.jsonl"), "--json")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["summary"]["verdict"], "FAIL")
        self.assertIn("findings", payload)

    def test_cli_missing_file_exit_code(self):
        completed = run_cli("validate_nav2_log.py", "no-such-log.jsonl")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("찾을 수 없습니다", completed.stderr)


class LabelDatasetTests(unittest.TestCase):
    def validate(self, dataset, config=None):
        return labels.validate_dataset(dataset, config=config)

    def test_clean_dataset_passes(self):
        dataset = {
            "dataset": "clean",
            "allowed_classes": ["car", "pedestrian"],
            "images": [
                {"id": "a", "path": "images/a.jpg", "width": 640, "height": 480,
                 "annotations": [{"class": "car", "bbox": [10, 20, 100, 80]}]},
                {"id": "b", "path": "images/b.jpg", "width": 640, "height": 480,
                 "annotations": [{"class": "pedestrian", "bbox": [0, 0, 640, 480]}]},
            ],
        }
        result = self.validate(dataset)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["images"], 2)
        self.assertEqual(result["annotations"], 2)
        self.assertEqual(result["summary"]["verdict"], "PASS")

    def test_missing_image_path_detected(self):
        dataset = {"images": [{"id": "a", "path": "", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [1, 1, 10, 10]}]}]}
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["IMAGE_MISSING"])
        self.assertEqual(result["summary"]["verdict"], "FAIL")

    def test_images_root_file_check(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [1, 1, 10, 10]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            missing = self.validate(dataset, labels.Config(images_root=Path(tmp)))
            self.assertEqual(codes(missing), ["IMAGE_MISSING"])
            (Path(tmp) / "a.jpg").write_bytes(b"fake")
            present = self.validate(dataset, labels.Config(images_root=Path(tmp)))
            self.assertEqual(present["findings"], [])

    def test_empty_annotation_is_warning(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": []}]}
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["EMPTY_ANNOTATION"])
        self.assertEqual(severity_of(result, "EMPTY_ANNOTATION"), ["warning"])
        self.assertEqual(result["summary"]["verdict"], "PASS")
        strict = self.validate(dataset, labels.Config(fail_on_warning=True))
        self.assertEqual(strict["summary"]["verdict"], "FAIL")

    def test_negative_coordinate_detected(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [10, -5, 50, 50]}]}]}
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["NEGATIVE_COORD"])

    def test_zero_size_box_detected(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [10, 10, 0, 50]}]}]}
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["INVALID_BBOX"])

    def test_out_of_range_box_detected(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [600, 100, 100, 50]}]}]}
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["BBOX_OUT_OF_RANGE"])

    def test_unknown_class_detected(self):
        dataset = {
            "allowed_classes": ["car", "pedestrian"],
            "images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "truck", "bbox": [10, 10, 50, 50]}]}],
        }
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["UNKNOWN_CLASS"])

    def test_missing_class_detected(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"bbox": [10, 10, 50, 50]}]}]}
        result = self.validate(dataset)
        self.assertIn("MISSING_CLASS", codes(result))

    def test_duplicate_image_id_detected(self):
        dataset = {
            "images": [
                {"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [1, 1, 10, 10]}]},
                {"id": "a", "path": "b.jpg", "width": 640, "height": 480, "annotations": [{"class": "car", "bbox": [1, 1, 10, 10]}]},
            ]
        }
        result = self.validate(dataset)
        self.assertEqual(codes(result), ["DUPLICATE_IMAGE_ID"])

    def test_class_override_from_cli_config(self):
        dataset = {"images": [{"id": "a", "path": "a.jpg", "width": 640, "height": 480, "annotations": [{"class": "bus", "bbox": [1, 1, 10, 10]}]}]}
        result = self.validate(dataset, labels.Config(allowed_classes=["car", "bus"]))
        self.assertEqual(result["findings"], [])

    def test_sample_labels_expected_findings(self):
        result = labels.run(ROOT / "samples" / "sample_labels.json")
        self.assertEqual(
            sorted(result["summary"]["by_code"].items()),
            sorted(
                [
                    ("BBOX_OUT_OF_RANGE", 1),
                    ("EMPTY_ANNOTATION", 1),
                    ("IMAGE_MISSING", 1),
                    ("INVALID_BBOX", 1),
                    ("NEGATIVE_COORD", 1),
                    ("UNKNOWN_CLASS", 1),
                ]
            ),
        )
        self.assertEqual(result["images"], 7)
        self.assertEqual(result["annotations"], 7)
        self.assertEqual(result["summary"]["verdict"], "FAIL")

    def test_cli_json_output_and_exit_code(self):
        completed = run_cli("validate_label_dataset.py", str(ROOT / "samples" / "sample_labels.json"), "--json")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["summary"]["verdict"], "FAIL")

    def test_cli_invalid_dataset_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"dataset": "x"}', encoding="utf-8")
            completed = run_cli("validate_label_dataset.py", str(bad))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("형식 오류", completed.stderr)


if __name__ == "__main__":
    unittest.main()

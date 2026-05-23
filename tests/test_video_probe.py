from __future__ import annotations

import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from worker.app.services.video_probe import VideoProbeService


class FakeVideoProbeService(VideoProbeService):
    def __init__(
        self,
        *,
        duration: Decimal = Decimal("26.92"),
        successful_attempt: int | None = 1,
        write_zero_bytes: set[int] | None = None,
    ) -> None:
        self.duration = duration
        self.successful_attempt = successful_attempt
        self.write_zero_bytes = write_zero_bytes or set()
        self.commands: list[list[str]] = []

    def get_video_duration_seconds(self, path: Path) -> Decimal:
        return self.duration

    def _run_ffmpeg(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        attempt = len(self.commands)
        output_path = Path(command[-1])
        if attempt in self.write_zero_bytes:
            output_path.write_bytes(b"")
            return subprocess.CompletedProcess(command, 0, "", "zero byte output")
        if self.successful_attempt == attempt:
            output_path.write_bytes(b"fake-png")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", f"failure {attempt}")


class VideoProbeLastFrameTests(unittest.TestCase):
    def test_extraction_does_not_seek_exactly_at_video_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FakeVideoProbeService(duration=Decimal("26.92"))
            output = service.extract_last_frame(
                Path(tmp_dir) / "segment.mp4",
                Path(tmp_dir) / "last.png",
            )

            first_command = service.commands[0]
            timestamp = first_command[first_command.index("-ss") + 1]
            self.assertEqual(timestamp, "26.720")
            self.assertNotEqual(timestamp, "26.920")
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_retries_multiple_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FakeVideoProbeService(
                duration=Decimal("26.92"),
                successful_attempt=3,
            )
            output = service.extract_last_frame(
                Path(tmp_dir) / "segment.mp4",
                Path(tmp_dir) / "last.png",
            )

            timestamps = [
                command[command.index("-ss") + 1]
                for command in service.commands
                if "-ss" in command
            ]
            self.assertEqual(timestamps, ["26.720", "26.420", "25.920"])
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_validates_output_file_exists_and_has_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FakeVideoProbeService(
                duration=Decimal("26.92"),
                successful_attempt=6,
                write_zero_bytes={1, 2, 3, 4, 5},
            )
            output = service.extract_last_frame(
                Path(tmp_dir) / "segment.mp4",
                Path(tmp_dir) / "last.png",
            )

            self.assertEqual(len(service.commands), 6)
            self.assertNotIn("-ss", service.commands[-1])
            self.assertIn("-sseof", service.commands[-1])
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()

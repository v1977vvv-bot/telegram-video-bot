from __future__ import annotations

import unittest
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from backend.app.services.batch_archive import parse_generation_batch_archive


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            payload = content.encode() if isinstance(content, str) else content
            archive.writestr(filename, payload)
    return buffer.getvalue()


def _error_codes(files: dict[str, bytes | str]) -> list[str]:
    result = parse_generation_batch_archive("batch.zip", _zip_bytes(files))
    return [error.code for error in result.errors]


class BatchArchiveParserTests(unittest.TestCase):
    def test_valid_latin_names(self) -> None:
        result = parse_generation_batch_archive(
            "batch.zip",
            _zip_bytes(
                {
                    "clip One.JPG": b"image",
                    "clip one.mp3": b"audio",
                }
            ),
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(result.pairs[0].index, 1)
        self.assertEqual(result.pairs[0].basename, "clip One")
        self.assertEqual(result.pairs[0].image_filename, "clip One.JPG")
        self.assertEqual(result.pairs[0].audio_filename, "clip one.mp3")

    def test_valid_cyrillic_names(self) -> None:
        result = parse_generation_batch_archive(
            "batch.zip",
            _zip_bytes(
                {
                    "Пример Фото.png": b"image",
                    "пример фото.WAV": b"audio",
                }
            ),
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(result.pairs[0].basename, "Пример Фото")

    def test_folders_match_by_basename(self) -> None:
        result = parse_generation_batch_archive(
            "batch.zip",
            _zip_bytes(
                {
                    "photos/001.jpg": b"image",
                    "audio/001.mp3": b"audio",
                }
            ),
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(result.pairs[0].image_filename, "photos/001.jpg")
        self.assertEqual(result.pairs[0].audio_filename, "audio/001.mp3")

    def test_ignored_service_files(self) -> None:
        result = parse_generation_batch_archive(
            "batch.zip",
            _zip_bytes(
                {
                    "__MACOSX/._001.jpg": b"ignored",
                    ".DS_Store": b"ignored",
                    "photos/.DS_Store": b"ignored",
                    "photos/._001.jpg": b"ignored",
                    "Thumbs.db": b"ignored",
                    "desktop.ini": b"ignored",
                    "001.jpg": b"image",
                    "001.mp3": b"audio",
                }
            ),
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.pairs), 1)

    def test_duplicate_image_basename(self) -> None:
        codes = _error_codes(
            {
                "photos/001.jpg": b"image",
                "other/ 001 .png": b"image",
                "audio/001.mp3": b"audio",
            }
        )

        self.assertIn("duplicate_image", codes)

    def test_missing_audio(self) -> None:
        self.assertIn("missing_audio", _error_codes({"001.jpg": b"image"}))

    def test_missing_image(self) -> None:
        self.assertIn("missing_image", _error_codes({"001.mp3": b"audio"}))

    def test_unsupported_file(self) -> None:
        self.assertIn(
            "unsupported_file",
            _error_codes(
                {
                    "001.jpg": b"image",
                    "001.mp3": b"audio",
                    "notes.txt": b"text",
                }
            ),
        )

    def test_unsafe_path(self) -> None:
        codes = _error_codes(
            {
                "../001.jpg": b"image",
                "audio/001.mp3": b"audio",
            }
        )

        self.assertIn("unsafe_path", codes)


if __name__ == "__main__":
    unittest.main()

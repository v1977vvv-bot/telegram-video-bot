from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import uuid4

from bot.app.services.backend_client import BotBackendClient


class BotBatchBackendClientTests(unittest.TestCase):
    def test_parse_batch_response(self) -> None:
        batch_id = uuid4()
        item_id = uuid4()
        job_id = uuid4()
        client = BotBackendClient.__new__(BotBackendClient)

        result = client._parse_batch(
            {
                "batch_id": str(batch_id),
                "status": "draft",
                "quality_profile": "720p",
                "total_jobs": 1,
                "total_duration_seconds": "7.000",
                "total_price_usd": "0.3600",
                "items": [
                    {
                        "item_id": str(item_id),
                        "index": 1,
                        "basename": "иван",
                        "image_filename": "иван.jpg",
                        "audio_filename": "иван.mp3",
                        "audio_duration_seconds": "7.000",
                        "price_usd": "0.3600",
                        "status": "draft",
                        "generation_job_id": str(job_id),
                    }
                ],
                "errors": [],
                "job_ids": [str(job_id)],
            }
        )

        self.assertEqual(result.batch_id, batch_id)
        self.assertEqual(result.quality_profile, "720p")
        self.assertEqual(result.total_price_usd, Decimal("0.3600"))
        self.assertEqual(result.items[0].basename, "иван")
        self.assertEqual(result.items[0].generation_job_id, job_id)
        self.assertEqual(result.job_ids, [job_id])


if __name__ == "__main__":
    unittest.main()

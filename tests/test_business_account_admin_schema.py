from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError

from backend.app.schemas.admin import BusinessAccountCreateRequest


class BusinessAccountCreateRequestTests(unittest.TestCase):
    def test_name_is_trimmed_and_zero_initial_balance_is_allowed(self) -> None:
        payload = BusinessAccountCreateRequest(
            name="  FireTraff  ",
            initial_balance_usd=Decimal("0"),
            reason="Create business account for FireTraff team",
        )

        self.assertEqual(payload.name, "FireTraff")
        self.assertEqual(payload.initial_balance_usd, Decimal("0"))

    def test_owner_telegram_id_and_owner_user_id_are_mutually_exclusive(self) -> None:
        with self.assertRaises(ValidationError):
            BusinessAccountCreateRequest(
                name="FireTraff",
                owner_telegram_id=778282290,
                owner_user_id=uuid4(),
                reason="Create business account for FireTraff team",
            )

    def test_negative_initial_balance_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            BusinessAccountCreateRequest(
                name="FireTraff",
                initial_balance_usd=Decimal("-1"),
                reason="Create business account for FireTraff team",
            )


if __name__ == "__main__":
    unittest.main()

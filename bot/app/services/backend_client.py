from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from shared.app.config import get_settings


class BackendClientError(Exception):
    pass


class BackendUnavailableError(BackendClientError):
    pass


class BackendNotFoundError(BackendClientError):
    pass


class BackendPaymentRequiredError(BackendClientError):
    pass


@dataclass(frozen=True, slots=True)
class BalanceDto:
    available_usd: Decimal
    frozen_usd: Decimal


@dataclass(frozen=True, slots=True)
class BusinessBalanceDto:
    id: UUID
    name: str
    available_usd: Decimal
    frozen_usd: Decimal


@dataclass(frozen=True, slots=True)
class TelegramUserDto:
    id: UUID
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    is_banned: bool
    balance: BalanceDto


@dataclass(frozen=True, slots=True)
class GenerationStatsDto:
    today: int
    month: int
    all_time: int
    completed_all_time: int
    failed_all_time: int


@dataclass(frozen=True, slots=True)
class SpendingStatsDto:
    today_usd: Decimal
    month_usd: Decimal
    all_time_usd: Decimal


@dataclass(frozen=True, slots=True)
class UserStatisticsDto:
    telegram_id: int
    balance: BalanceDto
    business_account: BusinessBalanceDto | None
    generations: GenerationStatsDto
    spending: SpendingStatsDto


@dataclass(frozen=True, slots=True)
class GenerationHistoryItemDto:
    id: UUID
    display_name: str
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal | None
    segments_count: int
    price_usd: Decimal | None
    error_message: str | None
    mock_result_message: str | None
    result_file_id: UUID | None
    result_url: str | None
    result_url_expires_in_seconds: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class GenerationFormatDto:
    label: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class GenerationDraftDto:
    job_id: UUID
    display_name: str
    status: str
    audio_duration_seconds: Decimal
    segments_count: int
    fps: int
    price_usd: Decimal
    available_formats: list[GenerationFormatDto]


@dataclass(frozen=True, slots=True)
class GenerationFormatSummaryDto:
    job_id: UUID
    display_name: str
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal
    segments_count: int
    price_usd: Decimal


@dataclass(frozen=True, slots=True)
class GenerationConfirmDto:
    job_id: UUID
    display_name: str
    status: str
    price_usd: Decimal
    message: str
    billing_account_type: str
    business_account_id: UUID | None
    business_account_name: str | None


@dataclass(frozen=True, slots=True)
class PaymentPackageDto:
    amount_usd: Decimal
    display_label: str
    provider_currency: str
    provider_amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentInvoiceDto:
    provider: str
    payment_id: UUID
    amount_usd: Decimal
    display_currency: str
    provider_currency: str
    provider_amount: Decimal
    payment_url: str
    status: str


class BotBackendClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.backend_internal_url.rstrip("/")
        self._timeout = httpx.Timeout(120.0, connect=5.0)

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> TelegramUserDto:
        payload = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": language_code,
        }
        data = await self._request("POST", "/api/v1/telegram/users/upsert", json=payload)
        return self._parse_user(data)

    async def get_statistics(self, telegram_id: int) -> UserStatisticsDto:
        data = await self._request("GET", f"/api/v1/users/by-telegram/{telegram_id}/statistics")
        return UserStatisticsDto(
            telegram_id=int(data["telegram_id"]),
            balance=self._parse_balance(data["balance"]),
            business_account=self._parse_business_balance(data.get("business_account")),
            generations=GenerationStatsDto(
                today=int(data["generations"]["today"]),
                month=int(data["generations"]["month"]),
                all_time=int(data["generations"]["all_time"]),
                completed_all_time=int(data["generations"]["completed_all_time"]),
                failed_all_time=int(data["generations"]["failed_all_time"]),
            ),
            spending=SpendingStatsDto(
                today_usd=Decimal(str(data["spending"]["today_usd"])),
                month_usd=Decimal(str(data["spending"]["month_usd"])),
                all_time_usd=Decimal(str(data["spending"]["all_time_usd"])),
            ),
        )

    async def get_generations(
        self,
        telegram_id: int,
        *,
        limit: int = 10,
    ) -> list[GenerationHistoryItemDto]:
        data = await self._request(
            "GET",
            f"/api/v1/users/by-telegram/{telegram_id}/generations",
            params={"limit": limit},
        )
        return [
            GenerationHistoryItemDto(
                id=UUID(item["id"]),
                display_name=str(item.get("display_name") or "Видео"),
                status=str(item["status"]),
                width=int(item["width"]),
                height=int(item["height"]),
                fps=int(item["fps"]),
                audio_duration_seconds=(
                    Decimal(str(item["audio_duration_seconds"]))
                    if item["audio_duration_seconds"] is not None
                    else None
                ),
                segments_count=int(item["segments_count"]),
                price_usd=Decimal(str(item["price_usd"]))
                if item["price_usd"] is not None
                else None,
                error_message=item.get("error_message"),
                mock_result_message=item.get("mock_result_message"),
                result_file_id=UUID(item["result_file_id"])
                if item.get("result_file_id") is not None
                else None,
                result_url=item.get("result_url"),
                result_url_expires_in_seconds=(
                    int(item["result_url_expires_in_seconds"])
                    if item.get("result_url_expires_in_seconds") is not None
                    else None
                ),
                created_at=str(item["created_at"]),
            )
            for item in data["items"]
        ]

    async def create_generation_draft(
        self,
        *,
        telegram_id: int,
        image_content: bytes,
        image_filename: str,
        image_mime_type: str,
        audio_content: bytes,
        audio_filename: str,
        audio_mime_type: str,
    ) -> GenerationDraftDto:
        data = await self._request(
            "POST",
            "/api/v1/generation/drafts",
            data={"telegram_id": str(telegram_id)},
            files={
                "image": (image_filename, image_content, image_mime_type),
                "audio": (audio_filename, audio_content, audio_mime_type),
            },
        )
        return GenerationDraftDto(
            job_id=UUID(data["job_id"]),
            display_name=str(data.get("display_name") or "Видео"),
            status=str(data["status"]),
            audio_duration_seconds=Decimal(str(data["audio_duration_seconds"])),
            segments_count=int(data["segments_count"]),
            fps=int(data["fps"]),
            price_usd=Decimal(str(data["price_usd"])),
            available_formats=[
                GenerationFormatDto(
                    label=str(item["label"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                )
                for item in data["available_formats"]
            ],
        )

    async def set_generation_format(
        self,
        *,
        job_id: UUID,
        telegram_id: int,
        width: int,
        height: int,
    ) -> GenerationFormatSummaryDto:
        data = await self._request(
            "PATCH",
            f"/api/v1/generation/drafts/{job_id}/format",
            json={"telegram_id": telegram_id, "width": width, "height": height},
        )
        return GenerationFormatSummaryDto(
            job_id=UUID(data["job_id"]),
            display_name=str(data.get("display_name") or "Видео"),
            status=str(data["status"]),
            width=int(data["width"]),
            height=int(data["height"]),
            fps=int(data["fps"]),
            audio_duration_seconds=Decimal(str(data["audio_duration_seconds"])),
            segments_count=int(data["segments_count"]),
            price_usd=Decimal(str(data["price_usd"])),
        )

    async def confirm_generation(self, *, job_id: UUID, telegram_id: int) -> GenerationConfirmDto:
        data = await self._request(
            "POST",
            f"/api/v1/generation/drafts/{job_id}/confirm",
            json={"telegram_id": telegram_id},
        )
        return GenerationConfirmDto(
            job_id=UUID(data["job_id"]),
            display_name=str(data.get("display_name") or "Видео"),
            status=str(data["status"]),
            price_usd=Decimal(str(data["price_usd"])),
            message=str(data["message"]),
            billing_account_type=str(data.get("billing_account_type") or "personal"),
            business_account_id=UUID(data["business_account_id"])
            if data.get("business_account_id") is not None
            else None,
            business_account_name=data.get("business_account_name"),
        )

    async def cancel_generation(self, *, job_id: UUID, telegram_id: int) -> GenerationConfirmDto:
        data = await self._request(
            "POST",
            f"/api/v1/generation/drafts/{job_id}/cancel",
            json={"telegram_id": telegram_id},
        )
        return GenerationConfirmDto(
            job_id=UUID(data["job_id"]),
            display_name=str(data.get("display_name") or "Видео"),
            status=str(data["status"]),
            price_usd=Decimal(str(data["price_usd"])),
            message=str(data["message"]),
            billing_account_type=str(data.get("billing_account_type") or "personal"),
            business_account_id=UUID(data["business_account_id"])
            if data.get("business_account_id") is not None
            else None,
            business_account_name=data.get("business_account_name"),
        )

    async def get_payment_packages(self) -> list[PaymentPackageDto]:
        data = await self._request("GET", "/api/v1/payments/packages")
        return [
            PaymentPackageDto(
                amount_usd=Decimal(str(item["amount_usd"])),
                display_label=str(item["display_label"]),
                provider_currency=str(item["provider_currency"]),
                provider_amount=Decimal(str(item["provider_amount"])),
            )
            for item in data["packages"]
        ]

    async def create_payment_invoice(
        self,
        *,
        telegram_id: int,
        amount_usd: Decimal,
    ) -> PaymentInvoiceDto:
        data = await self._request(
            "POST",
            "/api/v1/payments/invoices",
            json={"telegram_id": telegram_id, "amount_usd": str(amount_usd)},
        )
        return PaymentInvoiceDto(
            provider=str(data["provider"]),
            payment_id=UUID(data["payment_id"]),
            amount_usd=Decimal(str(data["amount_usd"])),
            display_currency=str(data["display_currency"]),
            provider_currency=str(data["provider_currency"]),
            provider_amount=Decimal(str(data["provider_amount"])),
            payment_url=str(data["payment_url"]),
            status=str(data["status"]),
        )

    async def debug_add_balance(
        self,
        *,
        telegram_id: int,
        amount_usd: Decimal,
        reason: str,
    ) -> BalanceDto:
        data = await self._request(
            "POST",
            f"/api/v1/debug/users/{telegram_id}/add-balance",
            json={"amount_usd": str(amount_usd), "reason": reason},
        )
        return self._parse_balance(data["balance"])

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendUnavailableError from exc

        if response.status_code == 404:
            raise BackendNotFoundError
        if response.status_code == 402:
            raise BackendPaymentRequiredError(self._extract_error_message(response))
        if response.status_code >= 500:
            raise BackendUnavailableError
        if response.status_code >= 400:
            raise BackendClientError(self._extract_error_message(response))
        return dict(response.json())

    def _extract_error_message(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
        return response.text

    def _parse_user(self, data: dict[str, Any]) -> TelegramUserDto:
        return TelegramUserDto(
            id=UUID(data["id"]),
            telegram_id=int(data["telegram_id"]),
            username=data["username"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            language_code=data["language_code"],
            is_banned=bool(data["is_banned"]),
            balance=self._parse_balance(data["balance"]),
        )

    def _parse_balance(self, data: dict[str, Any]) -> BalanceDto:
        return BalanceDto(
            available_usd=Decimal(str(data["available_usd"])),
            frozen_usd=Decimal(str(data["frozen_usd"])),
        )

    def _parse_business_balance(self, data: dict[str, Any] | None) -> BusinessBalanceDto | None:
        if data is None:
            return None
        return BusinessBalanceDto(
            id=UUID(data["id"]),
            name=str(data["name"]),
            available_usd=Decimal(str(data["available_usd"])),
            frozen_usd=Decimal(str(data["frozen_usd"])),
        )

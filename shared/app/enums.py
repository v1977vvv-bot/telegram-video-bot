from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    POD_STARTING = "pod_starting"
    UPLOADING_INPUTS = "uploading_inputs"
    GENERATING = "generating"
    STITCHING = "stitching"
    UPLOADING_RESULT = "uploading_result"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileType(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    SEGMENT_VIDEO = "segment_video"
    LAST_FRAME = "last_frame"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PAID_OVER = "paid_over"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class StorageProvider(StrEnum):
    CLOUDFLARE_R2 = "cloudflare_r2"
    LOCAL = "local"


class GenerationMode(StrEnum):
    MOCK = "mock"
    COMFYUI = "comfyui"


class BalanceTransactionType(StrEnum):
    DEPOSIT = "deposit"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    HOLD = "hold"
    CAPTURE = "capture"
    REFUND = "refund"
    RELEASE = "release"


class PodStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    IDLE = "idle"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

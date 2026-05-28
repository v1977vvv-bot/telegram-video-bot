from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    WAITING_FOR_GPU = "waiting_for_gpu"
    WAITING_FOR_POD = "waiting_for_pod"
    POD_STARTING = "pod_starting"
    UPLOADING_INPUTS = "uploading_inputs"
    GENERATING = "generating"
    STITCHING = "stitching"
    UPLOADING_RESULT = "uploading_result"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationBatchStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
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


class PaymentProvider(StrEnum):
    CRYPTOBOT = "cryptobot"
    CRYPTOMUS = "cryptomus"
    MANUAL = "manual"


class StorageProvider(StrEnum):
    CLOUDFLARE_R2 = "cloudflare_r2"
    LOCAL = "local"


class GenerationMode(StrEnum):
    MOCK = "mock"
    COMFYUI = "comfyui"


class VideoQuality(StrEnum):
    P480 = "480p"
    P720 = "720p"


class SegmentImageStrategy(StrEnum):
    LAST_FRAME = "last_frame"
    SOURCE_IMAGE = "source_image"


class AudioSegmentationStrategy(StrEnum):
    FIXED = "fixed"
    SILENCE = "silence"


class BalanceTransactionType(StrEnum):
    DEPOSIT = "deposit"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    HOLD = "hold"
    CAPTURE = "capture"
    REFUND = "refund"
    RELEASE = "release"


class BillingAccountType(StrEnum):
    PERSONAL = "personal"
    BUSINESS = "business"


class BusinessAccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class BusinessAccountMemberRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class BusinessBalanceTransactionType(StrEnum):
    MANUAL_TOPUP = "manual_topup"
    HOLD = "hold"
    CAPTURE = "capture"
    REFUND = "refund"
    RELEASE = "release"
    ADJUSTMENT = "adjustment"


class PodStatus(StrEnum):
    CREATING = "creating"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    IDLE = "idle"
    STOPPING = "stopping"
    TERMINATED = "terminated"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    QUEUED = "queued"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

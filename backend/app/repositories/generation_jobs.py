from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.generation_job import GenerationJob


class GenerationJobRepository:
    async def get_latest_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int,
    ) -> list[GenerationJob]:
        result = await session.execute(self._latest_stmt(user_id=user_id, limit=limit))
        return list(result.scalars().all())

    async def get_by_id(
        self,
        session: AsyncSession,
        job_id: UUID,
        *,
        with_segments: bool = False,
        for_update: bool = False,
    ) -> GenerationJob | None:
        statement = select(GenerationJob).where(GenerationJob.id == job_id)
        if with_segments:
            statement = statement.options(selectinload(GenerationJob.segments))
        if for_update:
            statement = statement.with_for_update()
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    def _latest_stmt(self, *, user_id: UUID, limit: int) -> Select[tuple[GenerationJob]]:
        return (
            select(GenerationJob)
            .where(GenerationJob.user_id == user_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(limit)
        )

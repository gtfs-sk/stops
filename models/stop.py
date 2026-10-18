from __future__ import annotations

from collections.abc import Mapping
from typing import override

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.amenity import Amenity
from models.base import Base
from models.stop_type import StopType
from models.transport_mode import TransportMode


class BaseStop(Base):
    __tablename__: str = "stops"

    __table_args__: tuple[CheckConstraint] = (
        CheckConstraint(
            """
            country <> 'SK' OR (
                NULLIF(TRIM(region), '') IS NOT NULL
                AND NULLIF(TRIM(district), '') IS NOT NULL
                AND NULLIF(TRIM(municipality), '') IS NOT NULL
            )
            """,
            name="sk_administrative_areas_required",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    district: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    municipality: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    stop_type: Mapped[StopType] = mapped_column(default=StopType.PLATFORM)
    modes: Mapped[list[TransportMode]] = mapped_column(JSON, default=list)
    amenities: Mapped[list[Amenity]] = mapped_column(JSON, default=list)

    is_station: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String, nullable=False)
    original_id: Mapped[str] = mapped_column(String, nullable=False)

    __mapper_args__: Mapping[str, object] = {
        "polymorphic_on": "stop_type",
        "with_polymorphic": "*",
    }


class Stop(BaseStop):
    __mapper_args__: Mapping[str, object] = {
        "polymorphic_identity": StopType.STATION,
    }

    platforms: Mapped[list[Platform]] = relationship(
        "Platform",
        back_populates="parent",
    )

    @override
    def __repr__(self) -> str:
        return f"<Stop(id='{self.id}', name='{self.name}')>"


class Platform(BaseStop):
    __mapper_args__: Mapping[str, object] = {
        "polymorphic_identity": StopType.PLATFORM,
    }

    platform_code: Mapped[str | None] = mapped_column(String)
    parent_station_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("stops.id")
    )

    parent: Mapped[Stop] = relationship(
        "Stop",
        back_populates="platforms",
        remote_side=lambda: [BaseStop.id],
    )

    def __init__(self, **kwargs: object) -> None:
        if "parent" in kwargs and "name" not in kwargs:
            parent_val = kwargs["parent"]
            if isinstance(parent_val, Stop):
                kwargs["name"] = parent_val.name

        super().__init__(**kwargs)

    @override
    def __repr__(self) -> str:
        return f"<Platform(id='{self.id}', code='{self.platform_code}', name='{self.name}')>"

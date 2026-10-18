from __future__ import annotations

from typing import final, override

from sqlalchemy import JSON, CheckConstraint, Enum, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.amenity import Amenity
from models.base import Base
from models.stop_kind import StopKind
from models.transport_mode import TransportMode


class LocationFields:
    """Shared columns, without joined or single-table inheritance."""

    id: Mapped[str] = mapped_column(String, primary_key=True)
    iscp_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
        comment="stop id from the iscp.sk registry",
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String, index=True)
    district: Mapped[str | None] = mapped_column(String, index=True)
    municipality: Mapped[str | None] = mapped_column(String, index=True)
    modes: Mapped[list[TransportMode]] = mapped_column(JSON, default=list)
    amenities: Mapped[list[Amenity]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String, nullable=False)
    original_id: Mapped[str] = mapped_column(String, nullable=False)


_SK_FIELDS_REQUIRED = """
country <> 'SK' OR (
    NULLIF(TRIM(region), '') IS NOT NULL
    AND NULLIF(TRIM(district), '') IS NOT NULL
    AND NULLIF(TRIM(municipality), '') IS NOT NULL
)
"""


@final
class Stop(LocationFields, Base):
    __tablename__ = "stops"
    __table_args__: tuple[CheckConstraint, ...] = (
        CheckConstraint(_SK_FIELDS_REQUIRED, name="sk_stop_admin_required"),
        CheckConstraint(
            "(kind = 'stop' AND lat IS NULL AND lon IS NULL) OR "
            "(kind = 'station' AND lat IS NOT NULL AND lon IS NOT NULL)",
            name="stop_coordinates_match_kind",
        ),
    )

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    kind: Mapped[StopKind] = mapped_column(
        Enum(
            StopKind,
            values_callable=lambda enum: [value.value for value in enum],
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=StopKind.STOP,
        nullable=False,
    )

    platforms: Mapped[list[Platform]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @override
    def __repr__(self) -> str:
        return f"<Stop(id={self.id!r}, kind={self.kind!r}, name={self.name!r})>"


@final
class Platform(LocationFields, Base):
    __tablename__ = "platforms"
    __table_args__: tuple[CheckConstraint] = (
        CheckConstraint(_SK_FIELDS_REQUIRED, name="sk_platform_admin_required"),
    )

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    stop_id: Mapped[str] = mapped_column(
        String, ForeignKey("stops.id"), nullable=False, index=True
    )
    platform_code: Mapped[str | None] = mapped_column(String)
    parent: Mapped[Stop] = relationship(back_populates="platforms")

    @override
    def __repr__(self) -> str:
        return (
            f"<Platform(id={self.id!r}, stop_id={self.stop_id!r}, "
            f"code={self.platform_code!r})>"
        )

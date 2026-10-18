from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from sqlalchemy.orm import Session


class BaseCleaner(ABC):
    name: str

    @abstractmethod
    def clean(self, session: Session) -> None:
        pass


def run_cleaners(session: Session, cleaners: Sequence[type[BaseCleaner]]) -> None:
    for cleaner_cls in cleaners:
        cleaner = cleaner_cls()
        cleaner.clean(session)
        session.commit()

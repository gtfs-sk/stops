from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from sqlalchemy.orm import Session


class BasePatcher(ABC):
    name: str

    @abstractmethod
    def patch(self, session: Session) -> None:
        pass


def run_patchers(session: Session, patchers: Sequence[type[BasePatcher]]) -> None:
    for patcher_cls in patchers:
        patcher = patcher_cls()
        patcher.patch(session)
        session.commit()

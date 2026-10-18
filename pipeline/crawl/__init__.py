from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from sqlalchemy.orm import Session

from models import Platform, Stop


class BaseCrawler(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> Iterator[Stop | Platform]:
        pass


def run_crawlers(session: Session, crawlers: Sequence[type[BaseCrawler]]) -> None:
    for crawler_cls in crawlers:
        crawler = crawler_cls()
        for item in crawler.fetch():
            session.add(item)

        session.commit()

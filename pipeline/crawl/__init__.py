from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from sqlalchemy.orm import Session

from models import Base


class BaseCrawler(ABC):
    """A source collector.

    Crawlers yield mapped database records of any model: the OSM crawler
    yields canonical Stop/Platform records, while reference-source
    crawlers (such as ISCP) yield their own first-class source records.
    Matching and cleaning never happen here.
    """

    name: str

    @abstractmethod
    def fetch(self) -> Iterator[Base]:
        pass


def run_crawlers(session: Session, crawlers: Sequence[type[BaseCrawler]]) -> None:
    for crawler_cls in crawlers:
        crawler = crawler_cls()
        for item in crawler.fetch():
            session.add(item)

        session.commit()

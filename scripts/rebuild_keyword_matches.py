"""Пересчитать совпадения старых статей по ключевым словам."""
from sqlalchemy import delete, select

from app.db import init_db, session_scope
from app.matching.keyword import KeywordMatcher
from app.models import Filter, Match
from app.pipeline import match_filter


def rebuild_keyword_matches() -> int:
    init_db()
    with session_scope() as session:
        keyword_filters = select(Filter).where(Filter.kind == KeywordMatcher.kind)
        filters = session.scalars(keyword_filters).all()
        filter_ids = keyword_filters.with_only_columns(Filter.id)
        session.execute(delete(Match).where(Match.filter_id.in_(filter_ids)))
        return sum(match_filter(session, flt, raise_on_match_error=True) for flt in filters)


if __name__ == "__main__":
    count = rebuild_keyword_matches()
    print(f"Пересчитано совпадений по ключевым словам: {count}")

import pytest
from sqlalchemy import delete, select, update

from app.db import session_scope
from app.matching import ArticleData, FilterData, MatchResult, get_matcher
from app.models import Article, Match
from scripts.rebuild_keyword_matches import rebuild_keyword_matches


def match_keyword(query, title="", summary=""):
    return get_matcher("keyword").match(
        ArticleData(id=1, title=title, summary=summary),
        FilterData(id=1, kind="keyword", query=query),
    )


def test_matches_in_both_fields_preserve_original_form():
    result = match_keyword("  самолёт  ", "САМОЛЁТОМ", "рейсы на самолетах")
    assert result == MatchResult(
        True,
        1.0,
        ["слово «самолёт» в заголовке: «САМОЛЁТОМ»", "слово «самолёт» в тексте: «самолетах»"],
    )


def test_phrase_matches_adjacent_words_in_another_order_and_keeps_punctuation():
    result = match_keyword("новые санкции", summary="Под санкциями, новыми для банка.")
    assert result == MatchResult(
        True, 1.0, ["фраза «новые санкции» в тексте: «санкциями, новыми»"]
    )


@pytest.mark.parametrize(
    ("query", "title", "summary"),
    [
        ("искусственный интеллект", "Искусственный", "Интеллект"),
        ("искусственный интеллект", "Искусственный. Интеллект", ""),
        ("искусственный интеллект", "Искусственный\nИнтеллект", ""),
        ("искусственный интеллект", "Искусственный общий интеллект", ""),
        ("банк банк", "Банк сообщил", ""),
        ("кошка", "Мошка", ""),  # оба слова есть в русском словаре
        ("кот", "Кт", ""),  # слишком короткое слово для допуска опечатки
        ("2026", "2027", ""),
        ("C++", "C#", ""),
        ("банк", "супербанкротство", ""),
        ("искусственый интелект", "Искусственный интеллект", ""),  # две опечатки на фразу
        ("", "Новости", ""),
        ("?!", "Новости", ""),
    ],
)
def test_non_matches(query, title, summary):
    assert match_keyword(query, title, summary) == MatchResult(matched=False)


@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("санкции", "санкци"),  # пропуск
        ("санкции", "санкцииями"),  # вставка в другой словоформе
        ("санкции", "санкцыи"),  # замена
        ("санции", "санкции"),  # пропуск в запросе
        ("санцкии", "санкциями"),  # перестановка в запросе и словоформа в статье
        ("искусственый интеллект", "искусственного интеллекта"),
        ("Python", "Pythno"),
    ],
)
def test_typos_preserve_original_text_in_reason(query, found):
    result = match_keyword(query, summary=found)
    label = "фраза" if " " in query else "слово"
    assert result == MatchResult(
        True, 1.0, [f"{label} «{query}» в тексте: «{found}» (совпадение с опечаткой)"]
    )


def test_prefers_exact_or_lemma_match_to_earlier_typo():
    result = match_keyword("санкции", "Санцкии, также о санкциях")
    assert result.reasons == ["слово «санкции» в заголовке: «санкциях»"]


def test_rebuild_updates_only_keyword_matches_and_can_be_run_twice(
    make_user, add_filter, post, feed,
):
    user_id = make_user()["id"]
    keyword = add_filter(user_id, "keyword", "санкции")
    add_filter(user_id, "topic", "санкции", threshold=0.1)
    post("Санкции")
    post("Переговоры о санкциях")
    # Имитируем старую БД: заглушка нашла только точную форму.
    with session_scope() as session:
        inflected = select(Article.id).where(Article.title == "Переговоры о санкциях")
        session.execute(
            delete(Match).where(Match.filter_id == keyword["id"], Match.article_id.in_(inflected))
        )
        session.execute(update(Match).where(Match.filter_id == keyword["id"]).values(
            reasons_json='["старая причина"]'
        ))
    initial = {item["title"]: item["matches"] for item in feed(user_id)}
    topic_match = next(match for match in initial["Санкции"] if match["kind"] == "topic")

    for _ in range(2):
        assert rebuild_keyword_matches() == 2
        matches = {item["title"]: item["matches"] for item in feed(user_id)}
        assert topic_match in matches["Санкции"]
        for title, form in (("Санкции", "Санкции"), ("Переговоры о санкциях", "санкциях")):
            reasons = [m["reasons"] for m in matches[title] if m["kind"] == "keyword"]
            assert reasons == [[f"слово «санкции» в заголовке: «{form}»"]]


def test_rebuild_keeps_previous_matches_when_engine_fails(
    monkeypatch, make_user, add_filter, post, feed,
):
    user_id = make_user()["id"]
    add_filter(user_id, "keyword", "санкции")
    post("Санкции")
    before = feed(user_id)

    def fail(*_):
        raise RuntimeError("ошибка движка")

    monkeypatch.setattr(get_matcher("keyword"), "match", fail)
    with pytest.raises(RuntimeError, match="ошибка движка"):
        rebuild_keyword_matches()

    assert feed(user_id) == before

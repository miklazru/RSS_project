"""Проверка контракта движков. Должна проходить и после замены заглушек настоящими движками."""
import pytest

from app.matching import FILTER_KINDS, ArticleData, FilterData, MatchResult, get_matcher

ARTICLE = ArticleData(id=1, title="Новые санкции против банков", summary="Курс рубля снизился")


@pytest.mark.parametrize("kind", FILTER_KINDS)
def test_matcher_returns_match_result(kind):
    matcher = get_matcher(kind)
    assert matcher.kind == kind
    result = matcher.match(ARTICLE, FilterData(id=1, kind=kind, query="санкции", threshold=0.1))
    assert isinstance(result, MatchResult)
    assert isinstance(result.matched, bool)
    assert isinstance(result.reasons, list)


@pytest.mark.parametrize("kind", FILTER_KINDS)
def test_matched_result_has_reasons(kind):
    matcher = get_matcher(kind)
    result = matcher.match(ARTICLE, FilterData(id=1, kind=kind, query="санкции", threshold=0.1))
    assert result.matched
    # без причин пользователь не поймёт, почему статья в ленте
    assert result.reasons
    assert all(isinstance(reason, str) and reason for reason in result.reasons)


def test_matcher_is_created_once():
    assert get_matcher("topic") is get_matcher("topic")


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        get_matcher("regex")


def test_keyword_stub_matches_whole_words_only():
    matcher = get_matcher("keyword")
    flt = FilterData(id=1, kind="keyword", query="банк")
    # «банков» — другая словоформа; заглушка её не находит, настоящий движок (роль 3) должен
    assert not matcher.match(ARTICLE, flt).matched
    assert matcher.match(ArticleData(id=2, title="Банк закрылся", summary=""), flt).matched

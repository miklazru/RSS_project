"""Список реальных лент для проверки сборщика и выгрузки статей."""

FEEDS = [
    # русские СМИ
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Лента.ру", "https://lenta.ru/rss/news"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
    ("Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("ТАСС", "https://tass.ru/rss/v2.xml"),
    ("Ведомости", "https://www.vedomosti.ru/rss/news"),
    ("РИА Новости", "https://ria.ru/export/rss2/archive/index.xml"),
    ("Российская газета", "https://rg.ru/xml/index.xml"),
    ("Хабр", "https://habr.com/ru/rss/articles/"),
    # Atom
    ("xkcd (Atom)", "https://xkcd.com/atom.xml"),
    ("GitHub commits (Atom)", "https://github.com/python/cpython/commits/main.atom"),
    ("Reddit worldnews (Atom)", "https://www.reddit.com/r/worldnews/.rss"),
    # зарубежные
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
]

# Эти ленты нужны для проверки формата Atom, но это не новости — в выгрузку для ролей 3 и 4 не берём
NOT_NEWS = {"xkcd (Atom)", "GitHub commits (Atom)"}

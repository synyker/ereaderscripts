"""Builds the news edition EPUB."""

from articles import Article


def group_into_sections(
    articles: list[Article],
    section_order: list[str],
    feed_sections: dict[str, str],
    fallback: str = "Muut",
) -> list[tuple[str, list[Article]]]:
    """Group articles into configured topic sections.

    Sections appear in `section_order`, with the fallback section last. Empty
    sections are omitted. Articles within a section are newest first.
    """
    buckets: dict[str, list[Article]] = {}
    for article in articles:
        section = feed_sections.get(article.feed, fallback)
        buckets.setdefault(section, []).append(article)

    ordered = [name for name in section_order if name in buckets]
    if fallback in buckets:
        ordered.append(fallback)

    return [
        (name, sorted(buckets[name], key=lambda a: a.published, reverse=True))
        for name in ordered
    ]

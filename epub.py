"""Builds the news edition EPUB."""

import logging

from articles import Article

log = logging.getLogger(__name__)


def group_into_sections(
    articles: list[Article],
    section_order: list[str],
    feed_sections: dict[str, str],
    fallback: str = "Muut",
) -> list[tuple[str, list[Article]]]:
    """Group articles into configured topic sections.

    Sections appear in `section_order`, with the fallback section last. Empty
    sections are omitted. Articles within a section are newest first.

    If a feed is configured for a section not in section_order and not the
    fallback, its articles are routed to the fallback section and a warning
    is logged to aid in diagnosing config errors.
    """
    valid_sections = set(section_order) | {fallback}
    buckets: dict[str, list[Article]] = {}
    for article in articles:
        section = feed_sections.get(article.feed, fallback)
        # If the section is not valid, route to fallback and warn
        if section not in valid_sections:
            log.warning(
                f"Feed '{article.feed}' is configured for unknown section '{section}'; "
                f"routing to fallback section '{fallback}'"
            )
            section = fallback
        buckets.setdefault(section, []).append(article)

    ordered = [name for name in section_order if name in buckets]
    if fallback in buckets:
        ordered.append(fallback)

    return [
        (name, sorted(buckets[name], key=lambda a: a.published, reverse=True))
        for name in ordered
    ]

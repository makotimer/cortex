# modules/bible_plan/links.py
from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import quote_plus

from modules._shared import utils

__all__ = ["linkify_scripture_refs", "nkjv_link"]


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# Full-name books → YouVersion 3-letter codes (NKJV=114)
book_codes = {
    "Genesis": "GEN",
    "Exodus": "EXO",
    "Leviticus": "LEV",
    "Numbers": "NUM",
    "Deuteronomy": "DEU",
    "Joshua": "JOS",
    "Judges": "JDG",
    "Ruth": "RUT",
    "1 Samuel": "1SA",
    "2 Samuel": "2SA",
    "1 Kings": "1KI",
    "2 Kings": "2KI",
    "1 Chronicles": "1CH",
    "2 Chronicles": "2CH",
    "Ezra": "EZR",
    "Nehemiah": "NEH",
    "Esther": "EST",
    "Job": "JOB",
    "Psalms": "PSA",
    "Psalm": "PSA",
    "Proverbs": "PRO",
    "Ecclesiastes": "ECC",
    "Song of Solomon": "SNG",
    "Isaiah": "ISA",
    "Jeremiah": "JER",
    "Lamentations": "LAM",
    "Ezekiel": "EZK",
    "Daniel": "DAN",
    "Hosea": "HOS",
    "Joel": "JOL",
    "Amos": "AMO",
    "Obadiah": "OBA",
    "Jonah": "JON",
    "Micah": "MIC",
    "Nahum": "NAM",
    "Habakkuk": "HAB",
    "Zephaniah": "ZEP",
    "Haggai": "HAG",
    "Zechariah": "ZEC",
    "Malachi": "MAL",
    "Matthew": "MAT",
    "Mark": "MRK",
    "Luke": "LUK",
    "John": "JHN",
    "Acts": "ACT",
    "Romans": "ROM",
    "1 Corinthians": "1CO",
    "2 Corinthians": "2CO",
    "Galatians": "GAL",
    "Ephesians": "EPH",
    "Philippians": "PHP",
    "Colossians": "COL",
    "1 Thessalonians": "1TH",
    "2 Thessalonians": "2TH",
    "1 Timothy": "1TI",
    "2 Timothy": "2TI",
    "Titus": "TIT",
    "Philemon": "PHM",
    "Hebrews": "HEB",
    "James": "JAS",
    "1 Peter": "1PE",
    "2 Peter": "2PE",
    "1 John": "1JN",
    "2 John": "2JN",
    "3 John": "3JN",
    "Jude": "JUD",
    "Revelation": "REV",
}

# --------------------------------------------------------------------------------------
# Canonicalization helpers
# --------------------------------------------------------------------------------------

# Minimal canonical book names suitable for URL queries (BibleGateway works well).
# We intentionally use "Psalm" (singular) for single-chapter references; "Psalms" also resolves.
_CANONICAL = {
    # Pentateuch
    "genesis": "Genesis",
    "exodus": "Exodus",
    "leviticus": "Leviticus",
    "numbers": "Numbers",
    "deuteronomy": "Deuteronomy",
    # Histories
    "joshua": "Joshua",
    "judges": "Judges",
    "ruth": "Ruth",
    "1 samuel": "1 Samuel",
    "2 samuel": "2 Samuel",
    "1 kings": "1 Kings",
    "2 kings": "2 Kings",
    "1 chronicles": "1 Chronicles",
    "2 chronicles": "2 Chronicles",
    "ezra": "Ezra",
    "nehemiah": "Nehemiah",
    "esther": "Esther",
    # Wisdom/Poetry
    "job": "Job",
    "psalm": "Psalm",
    "psalms": "Psalms",
    "proverbs": "Proverbs",
    "ecclesiastes": "Ecclesiastes",
    "song of solomon": "Song of Solomon",
    "song": "Song of Solomon",
    # Major Prophets
    "isaiah": "Isaiah",
    "jeremiah": "Jeremiah",
    "lamentations": "Lamentations",
    "ezekiel": "Ezekiel",
    "daniel": "Daniel",
    # Minor Prophets
    "hosea": "Hosea",
    "joel": "Joel",
    "amos": "Amos",
    "obadiah": "Obadiah",
    "jonah": "Jonah",
    "micah": "Micah",
    "nahum": "Nahum",
    "habakkuk": "Habakkuk",
    "zephaniah": "Zephaniah",
    "haggai": "Haggai",
    "zechariah": "Zechariah",
    "malachi": "Malachi",
    # Gospels/Acts
    "matthew": "Matthew",
    "mark": "Mark",
    "luke": "Luke",
    "john": "John",
    "acts": "Acts",
    # Paul
    "romans": "Romans",
    "1 corinthians": "1 Corinthians",
    "2 corinthians": "2 Corinthians",
    "galatians": "Galatians",
    "ephesians": "Ephesians",
    "philippians": "Philippians",
    "colossians": "Colossians",
    "1 thessalonians": "1 Thessalonians",
    "2 thessalonians": "2 Thessalonians",
    "1 timothy": "1 Timothy",
    "2 timothy": "2 Timothy",
    "titus": "Titus",
    "philemon": "Philemon",
    # General
    "hebrews": "Hebrews",
    "james": "James",
    "1 peter": "1 Peter",
    "2 peter": "2 Peter",
    "1 john": "1 John",
    "2 john": "2 John",
    "3 john": "3 John",
    "jude": "Jude",
    "revelation": "Revelation",
}

# Common abbreviations/variants → canonical keys used above
# (All keys are lowercased; numerals may be spaced or not in matching regex below.)
_ALIASES = {
    # Short OT
    "gen": "genesis",
    "ex": "exodus",
    "lev": "leviticus",
    "num": "numbers",
    "deut": "deuteronomy",
    "josh": "joshua",
    "judg": "judges",
    "1 sam": "1 samuel",
    "2 sam": "2 samuel",
    "1 kgs": "1 kings",
    "2 kgs": "2 kings",
    "1 chr": "1 chronicles",
    "2 chr": "2 chronicles",
    "neh": "nehemiah",
    "esth": "esther",
    "ps": "psalms",
    "psalm": "psalm",
    "prov": "proverbs",
    "eccl": "ecclesiastes",
    "song": "song of solomon",
    "isa": "isaiah",
    "jer": "jeremiah",
    "lam": "lamentations",
    "ezek": "ezekiel",
    "dan": "daniel",
    "hos": "hosea",
    "obad": "obadiah",
    "mic": "micah",
    "hab": "habakkuk",
    "zeph": "zephaniah",
    "hag": "haggai",
    "zech": "zechariah",
    "mal": "malachi",
    # Short NT
    "matt": "matthew",
    "mk": "mark",
    "mrk": "mark",
    "lk": "luke",
    "lu": "luke",
    "jn": "john",
    "jhn": "john",
    "joh": "john",
    "act": "acts",
    "rom": "romans",
    "1 cor": "1 corinthians",
    "2 cor": "2 corinthians",
    "eph": "ephesians",
    "gal": "galatians",
    "phil": "philippians",
    "col": "colossians",
    "1 thess": "1 thessalonians",
    "2 thess": "2 thessalonians",
    "1 tim": "1 timothy",
    "2 tim": "2 timothy",
    "tit": "titus",
    "phlm": "philemon",
    "heb": "hebrews",
    "jas": "james",
    "jms": "james",
    "1 pet": "1 peter",
    "2 pet": "2 peter",
    "1 jn": "1 john",
    "2 jn": "2 john",
    "3 jn": "3 john",
    "rev": "revelation",
}


def _canonicalize_book(book: str) -> str:
    """
    Normalize a book string to a canonical display name acceptable to BibleGateway.
    """
    b = book.strip().lower().replace("  ", " ")
    # Normalize runs like "1John" → "1 john"
    b = re.sub(r"^([123])\s*(\w)", r"\1 \2", b)

    # Try direct canonical mapping
    if b in _CANONICAL:
        return _CANONICAL[b]

    # Try alias mapping
    if b in _ALIASES:
        return _CANONICAL[_ALIASES[b]]

    # Fallback: title-case words, keep leading numeral if present
    return re.sub(r"(^[123])\s*(\w.*)$", lambda m: f"{m.group(1)} {m.group(2).title()}", b.title())


# --------------------------------------------------------------------------------------
# NKJV link builder
# --------------------------------------------------------------------------------------


def nkjv_link(book_name: str, chapter: int, start_verse: int | None = None, end_verse: int | None = None) -> str:
    """
    Build a stable NKJV passage link (BibleGateway).
    Examples:
      nkjv_link("John", 3, 16, None)  -> ?search=John%203%3A16&version=NKJV
      nkjv_link("Psalm", 23)          -> ?search=Psalm%2023&version=NKJV
      nkjv_link("1 Jn", 4, 7, 8)      -> ?search=1%20John%204%3A7-8&version=NKJV
    """
    if end_verse is not None and start_verse is None:
        raise ValueError("End verse cannot be specified without a start verse.")

    book_code = book_codes[_canonicalize_book(book_name)]
    url = f"https://www.bible.com/bible/114/{book_code}.{chapter}"
    if start_verse is not None:
        url += f".{start_verse}"
        if end_verse is not None:
            url += f"-{end_verse}"

    # Link text
    link_text = f"{book_name} {chapter}"
    if start_verse is not None:
        link_text += f":{start_verse}"
        if end_verse is not None:
            link_text += f"-{end_verse}"

    return f'<a href="{_esc(url)}">{_esc(link_text)}</a>'


# --------------------------------------------------------------------------------------
# Inline linkification (precompiled pattern)
# --------------------------------------------------------------------------------------

# A balanced pattern that recognizes:
#   - Full names: "1 Corinthians", "Psalm(s)", etc.
#   - Short forms: "1 Cor", "Ps", "Jn", etc. (with or without space after numeral)
#   - En dash (–) or hyphen (-) for verse ranges  # noqa: RUF003
# Display preserves the original matched text; href uses canonicalized form.
# NOTE: Keep most-specific names earlier when you expand.
_BOOK_PAT = (
    r"(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|"
    r"1\s*Samuel|2\s*Samuel|1\s*Kings|2\s*Kings|1\s*Chronicles|2\s*Chronicles|"
    r"Ezra|Nehemiah|Esther|Job|Psalm(?:s)?|Proverbs|Ecclesiastes|Song\s+of\s+Solomon|"
    r"Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|Amos|Obadiah|Jonah|Micah|"
    r"Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|Malachi|Matthew|Mark|Luke|John|"
    r"Acts|Romans|1\s*Corinthians|2\s*Corinthians|Galatians|Ephesians|Philippians|"
    r"Colossians|1\s*Thessalonians|2\s*Thessalonians|1\s*Timothy|2\s*Timothy|Titus|"
    r"Philemon|Hebrews|James|1\s*Peter|2\s*Peter|1\s*John|2\s*John|3\s*John|Jude|Revelation|"
    # short forms (OT)
    r"Gen|Ex|Lev|Num|Deut|Josh|Judg|1\s*Sam|2\s*Sam|1\s*Kgs|2\s*Kgs|1\s*Chr|2\s*Chr|"
    r"Neh|Esth|Ps(?:alm)?|Prov|Eccl|Song|Isa|Jer|Lam|Ezek|Dan|Hos|Obad|Mic|"
    r"Nah|Hab|Zeph|Hag|Zech|Mal|"
    # short forms (NT)
    r"Matt|Mk|Mrk|Lk|Lu|Jn|Jhn|Joh|Act|Rom|1\s*Cor|2\s*Cor|Gal|Eph|Phil|Col|"
    r"1\s*Thess|2\s*Thess|1\s*Tim|2\s*Tim|Tit|Phlm|Heb|Jas|Jms|"
    r"1\s*Pet|2\s*Pet|1\s*Jn|2\s*Jn|3\s*Jn|Rev)"
)


def linkify_scripture_refs(text: str) -> str:
    """
    Replace references like 'John 3:16-18' or 'Genesis 1' with <a> links to NKJV on YouVersion.
    """
    book_pattern = (
        r"\b(" + "|".join(re.escape(name) for name in book_codes) + r")\b\s+(\d+)(?::(\d+)(?:[-–](\d+))?)?"  # noqa: RUF001
    )

    def repl(m):
        book = m.group(1)
        chapter = int(m.group(2))
        start_verse = int(m.group(3)) if m.group(3) else None
        end_verse = int(m.group(4)) if m.group(4) else None
        try:
            return nkjv_link(book, chapter, start_verse, end_verse)
        except ValueError:
            return m.group(0)

    return re.sub(book_pattern, repl, text)

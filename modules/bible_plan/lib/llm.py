# ruff: noqa: E501

from __future__ import annotations

from .links import linkify_scripture_refs


def generate_commentary(
    *,
    book: str,
    chapter: int,
    prev_book: str,
    prev_chapter: int,
    calvin_url: str | None,
    mh_url: str | None,
    model_env: str,
    temp_env: str,
    enable: bool,
) -> str | None:
    if not enable:
        return None
    try:
        from modules._shared import utils  # your thin facade

        llm = utils.OpenAIChat(model_env=model_env, temp_env=temp_env)
        system = (
            "You are a Reformed theologian trained in the teachings of Augustine, Calvin, Knox, "
            "and modern pastors like R.C. Sproul, Joel Beeke, and Sinclair Ferguson. "
            "Follow these principles:\n"
            " - Use a redemptive-historical and covenantal lens\n"
            " - Reflect confessional Reformed theology (e.g., RPCNA testimony alongside the Westminster Confession)\n"
            " - Do not take verses out of context; respect historical/literary setting\n"
            " - Highlight fulfillment in Christ where appropriate\n"
            " - Use Scripture to interpret Scripture (responsible intertextual insight)\n"
            " - Avoid allegory, moralism, or speculation\n"
            "Style: Presbyterian, pastoral, succinct; clear headings and bullet points welcome."
        )

        user = (
            f"Using commentaries at {calvin_url} and {mh_url}, generate the following sections, leaning on Calvin's findings if there "
            "is much difference.  Do not call out Calvin or Matthew Henry specifically.  Use the exact section titles."
            f"These are covering {book} {chapter}, but tie into yesterday's reading on {prev_book} {prev_chapter} a bit as applicable.\n"
            "1. Exposition and Merged Commentary\n"
            " - Bulk of the content\n"
            "2. Context Notes\n"
            " - Where applicable, as noted in commentaries or found in other materials\n"
            "3. Christological and Covenantal Threads\n"
            "4. Practical Applications\n"
            "5. Cross-references\n"
            "6. Family Worship\n"
            " - Suggest reading a portion of the chapter or its entirety, some questions to ask elementary aged children, and a psalm to sing.\n"
            "7. Discussion Points\n"
            " - Brief comments on discussions points with Christian friends or a spouse\n"
            "Avoid repetition between sections unless necessary, so split the content of the merged commentary into sections #2 to #4 as appropriate.\n"
            "Use modest citations (book chapter:verse) when referencing other passages, always using full names for books of the Bible, "
            "if specifying a verse separate the chapter and verse by a colon, and use a `-` to separate verses if specifying a range. "
            "Scripture text is already shown above so do not quote/include the chapter.\n"
            "Format the response with full markdown, differing heading sizes and emphasizing text as appropriate.\n"
            "Do not provide a summarizing preamble or postscript for this request, only the sections requested. "
            "Avoid restating instructions when generating content."
            "Review your output for terms that a modern general audience would likely find unfamiliar—particularly those "
            "drawn from ancient history, theology, or academic discourse. Flag any such words or phrases that would "
            "interrupt natural reading, and benefit from a brief definition, and add definitions. Exclude the following from the definitions:"
            "\n - common moral, cultural, or legal vocabulary that an educated adult or regular churchgoer would already recognize."
            "\n - words already sufficiently defined in your output."
            "\n - any of: Redemptive-historical, Covenantal, Covenant, Typology, Election, Genealogy, Eschatological."
        )
        md = llm.chat(system, user)
        html = utils.md_to_html(md)
        return linkify_scripture_refs(html)
    except Exception:
        return None

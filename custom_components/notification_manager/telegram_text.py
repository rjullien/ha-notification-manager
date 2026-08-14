"""Telegram parse-mode constants and text-escaping helpers.

Why this module exists
----------------------
Home Assistant's ``telegram_bot`` platform defaults its parser to *markdown*::

    vol.Optional(ATTR_PARSER, default=PARSER_MD): cv.string   # platform schema

and ``TelegramNotificationService._get_msg_kwargs`` only overrides that default
when the service data explicitly carries a ``parse_mode`` key::

    params = {ATTR_PARSER: self._parse_mode, ...}       # bot default = markdown
    if ATTR_PARSER in data:
        params[ATTR_PARSER] = self._parsers.get(data[ATTR_PARSER], self._parse_mode)

Consequence: *omitting* ``parse_mode`` does not mean "plain text", it means
"markdown". Any message carrying an unbalanced ``_`` or ``*`` — entity_ids,
snake_case labels, file names, Windows paths — is then rejected by the Telegram
API with ``Can't parse entities``, and the notification is silently lost.

The two ways out, both provided here:

``PARSE_MODE_PLAIN``
    The sentinel ``telegram_bot`` maps to ``parse_mode=None`` (see ``_parsers``:
    ``{PARSER_PLAIN_TEXT: None}``). Send it explicitly whenever the caller did
    not ask for formatting, so raw user text is never parsed.

``escape_html`` + ``PARSE_MODE_HTML``
    For the messages we *do* format on purpose. HTML is preferred over legacy
    Markdown because legacy Markdown has no escape syntax at all (only
    MarkdownV2 does), whereas HTML escaping is well defined and ``_``/``*``
    carry no meaning in it.
"""
from __future__ import annotations

from html import escape as _html_escape

# Maps to parse_mode=None in telegram_bot — no parsing whatsoever.
PARSE_MODE_PLAIN = "plain_text"

# Well-defined escaping, and "_"/"*" are inert. Preferred for formatted messages.
PARSE_MODE_HTML = "html"


def escape_html(value: object) -> str:
    """Escape ``value`` for safe interpolation into a ``parse_mode: html`` message.

    Escapes the three characters Telegram's HTML parser treats as markup
    (``&``, ``<``, ``>``). Quotes are left alone: they are only significant
    inside tag attributes, which we never build from dynamic data, and escaping
    them would make friendly names unreadable.

    Args:
        value: Any value to interpolate (coerced to ``str``); ``None`` → ``""``.

    Returns:
        The escaped string.
    """
    if value is None:
        return ""
    return _html_escape(str(value), quote=False)

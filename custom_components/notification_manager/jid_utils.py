"""WhatsApp JID conversion helpers.

Centralises JID-to-phone conversion so individual contacts and groups are
handled consistently across the WhatsApp send path.
"""


def jid_to_phone(jid: str) -> str:
    """Convert a WhatsApp JID to the phone value expected by the GoWA bridge.

    Individual contacts use the ``@s.whatsapp.net`` suffix: it is stripped so
    only the phone number is sent. Group JIDs (``@g.us``) have no phone-number
    equivalent, so they are passed through unchanged — GoWA accepts them
    directly in the phone field.

    Using ``endswith`` instead of ``in`` avoids false positives if a JID ever
    contained the substring in an unexpected position.
    """
    if jid.endswith("@s.whatsapp.net"):
        return jid[: -len("@s.whatsapp.net")]
    return jid

"""WhatsApp JID helpers.

Bug fixed: _jid_to_phone only stripped the @s.whatsapp.net suffix via
``in`` checks, which is brittle. Group JIDs (@g.us) must pass through
unchanged so the GoWA bridge receives them as-is.
"""


def jid_to_phone(jid: str) -> str:
    """Convert a WhatsApp JID to the phone number expected by GoWA.

    - Individual contacts: ``33600000001@s.whatsapp.net`` → ``33600000001``
    - Groups: ``120363000000000000@g.us`` → unchanged
    """
    if jid.endswith("@s.whatsapp.net"):
        return jid[: -len("@s.whatsapp.net")]
    return jid

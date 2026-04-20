"""Time-of-day greeting + short positive motivational lines.

Kept deliberately light and friendly — mild encouragement, gentle
spreadsheet / tea humour, and the odd proverb. Nothing intense,
parasocial, or performative.
"""
from __future__ import annotations

import random
from datetime import datetime


QUOTES: tuple[str, ...] = (
    # Light, friendly encouragement
    "Morning! The kettle believes in you.",
    "One row at a time. That's the rule.",
    "Ready when you are.",
    "Slow and steady wins the ledger.",
    "A cup of tea makes everything easier.",
    "Take your time — the numbers aren't going anywhere.",
    "Nothing a biscuit can't improve.",
    "Another day, another tidy spreadsheet.",
    "Row by row, onwards.",
    "Half the job is showing up. Nice one.",
    "Category by category — easier than it looks.",
    "Let's see what today's statements have in store.",
    "Tea first. Then the ledger.",
    "No rush. Just right.",
    "Neat numbers, tidy mind.",
    "Deep breath. We go again.",
    "A quiet afternoon of careful work — lovely.",
    "Small focus, big difference.",
    "Good morning — or whenever you're reading this.",
    "Ready for a productive session?",
    # Gentle proverbs and wordplay
    "Measure twice, categorise once.",
    "The early broker catches the biscuit.",
    "A spreadsheet in time saves nine.",
    "Many a tidy ledger makes a mickle.",
    "Rome wasn't categorised in a day.",
    "Don't count your mortgages before they're approved.",
    "Where there's a will, there's a column.",
    "Look before you leap to conclusions.",
    "Every cloud has a silver spreadsheet.",
    "Practice makes progress.",
    # Mild mortgage / number humour
    "Numbers behave when you're polite to them.",
    "Spreadsheets respect those who are patient with them.",
    "Decimals appreciate attention.",
    "Commas are the unsung heroes of finance.",
    "A tidy column is a tidy conscience.",
    "Even accountants enjoy a pun. Quietly.",
    "'Interesting' is an adjective numbers have earned.",
    "Two's company. Three's a reconciliation.",
    "A ledger in hand is worth two in the drawer.",
    "If in doubt, double-check. If still in doubt, biscuit.",
    # Calm reminders
    "Be kind to your eyes — stretch now and then.",
    "Good posture first, categories second.",
    "If the screen blurs, step away for a minute.",
    "Take a walk later. The files will wait.",
    "Remember to blink. Really.",
    "Biscuit break is self-care.",
    "Finish this one row. That's enough for now.",
    "Brew a cuppa. Start with one transaction.",
    "If it stops being fun, take five.",
    "Shoulders down. Jaw relaxed. Carry on.",
    # Light lyrical nods (traditional / folk)
    "Every little helps.",
    "Slow and steady — that old tortoise was onto something.",
    "One step at a time, as the song goes.",
    "Keep calm and categorise on.",
    "Don't worry, be thorough.",
    "Let it be — unless the category's wrong.",
    # Mild self-deprecation (about the app)
    "The AI is trying its best. Please correct it kindly.",
    "If it gets a category wrong, it's learning. Honest.",
    "Teach the robot. It's how it grows.",
    "Trust but verify — that's our motto.",
    # Positive without being performative
    "Good work is quiet work.",
    "A friendly nudge: you're doing fine.",
    "Progress counts, however small.",
    "Another file done is a small win.",
    "Today's corrections are tomorrow's auto-categories.",
    "One careful pass is better than two rushed ones.",
    "Keep going, gently.",
    "You've got this — no drama required.",
    "Fine weather for ledgers today.",
    "Clean data, calm life.",
)


def _time_of_day(now: datetime | None = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def greeting_for(display_name: str, *, now: datetime | None = None) -> str:
    """Return 'Good morning, Jane' (etc.) based on the local clock."""
    part = _time_of_day(now)
    clean = (display_name or "").strip() or "there"
    return f"Good {part}, {clean}"


def random_quote(rng: random.Random | None = None) -> str:
    r = rng or random
    return r.choice(QUOTES)

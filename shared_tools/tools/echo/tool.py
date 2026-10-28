def echo(text: str, shout: bool = False) -> str:
    """Echo back the given text, optionally shouting it."""
    return text.upper() if shout else text

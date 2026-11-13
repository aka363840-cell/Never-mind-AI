class GameError(Exception):
    """User-facing, expected failure. Message is safe to show."""


class InsufficientFunds(GameError):
    pass


class OnCooldown(GameError):
    def __init__(self, seconds: int, what: str = "That"):
        from utils.fmt import dur
        super().__init__(f"⏳ {what} is on cooldown. Try again in <b>{dur(seconds)}</b>.")
        self.seconds = seconds

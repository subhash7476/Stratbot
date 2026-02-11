from dataclasses import dataclass


@dataclass(frozen=True)
class Greeks:
    """
    Represents the risk sensitivities (Greeks) of a financial instrument.
    """
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float

    def __add__(self, other: 'Greeks') -> 'Greeks':
        return Greeks(
            delta=self.delta + other.delta,
            gamma=self.gamma + other.gamma,
            vega=self.vega + other.vega,
            theta=self.theta + other.theta,
            rho=self.rho + other.rho
        )

    def __mul__(self, scalar: float) -> 'Greeks':
        return Greeks(
            delta=self.delta * scalar,
            gamma=self.gamma * scalar,
            vega=self.vega * scalar,
            theta=self.theta * scalar,
            rho=self.rho * scalar
        )

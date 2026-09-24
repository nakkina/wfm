"""Erlang C staffing for one interval.

Erlang C assumes Poisson arrivals, exponential handle times, infinite patience and a
stationary interval. It ignores abandonment, so results are modelled estimates of
interval staffing — not guarantees of real-world service levels.

Numerics: the probability of waiting is computed from the Erlang B recurrence
    B(0) = 1,  B(n) = A·B(n−1) / (n + A·B(n−1))
    C(N) = N·B(N) / (N − A·(1 − B(N)))
which never forms large factorials or powers and is stable for loads in the thousands.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ErlangResult:
    agents: int  # required productive agents N (0 when there is no workload)
    service_level: float  # expected share answered within the threshold at N agents
    occupancy: float  # A / N
    wait_probability: float  # Erlang C probability that a call waits


def erlang_b(agents: int, load: float) -> float:
    b = 1.0
    for n in range(1, agents + 1):
        b = load * b / (n + load * b)
    return b


def erlang_c(agents: int, load: float) -> float:
    """Probability that an arriving call waits; 1.0 when the queue is unstable (N ≤ A)."""
    if load <= 0:
        return 0.0
    if agents <= load:
        return 1.0
    b = erlang_b(agents, load)
    return agents * b / (agents - load * (1 - b))


def service_level(agents: int, load: float, aht_seconds: float, threshold_seconds: float) -> float:
    if load <= 0:
        return 1.0
    if agents <= load:
        return 0.0
    wait = erlang_c(agents, load)
    return 1 - wait * math.exp(-(agents - load) * threshold_seconds / aht_seconds)


def required_agents(
    load: float,
    aht_seconds: float,
    target_service_level: float,
    threshold_seconds: float,
    max_occupancy: float,
) -> ErlangResult:
    """Smallest N > load meeting both the service-level target and the occupancy cap."""
    if load < 0 or not math.isfinite(load):
        raise ValueError(f"Offered load must be a finite non-negative number, got {load}")
    if load == 0:
        return ErlangResult(agents=0, service_level=1.0, occupancy=0.0, wait_probability=0.0)
    if aht_seconds <= 0:
        raise ValueError("AHT must be positive when there is workload")

    # Occupancy cap gives a direct lower bound; start there (and above the load).
    n = max(math.floor(load) + 1, math.ceil(load / max_occupancy))
    while True:
        sl = service_level(n, load, aht_seconds, threshold_seconds)
        if sl >= target_service_level and load / n <= max_occupancy + 1e-12:
            return ErlangResult(
                agents=n,
                service_level=sl,
                occupancy=load / n,
                wait_probability=erlang_c(n, load),
            )
        n += 1


def on_phone_agents(productive_agents: int, residual_shrinkage: float) -> int:
    """Scheduled on-phone agents so that, after unplanned shrinkage, N remain productive.

    Applied once. Breaks, meals and leave are scheduled explicitly and not included here.
    """
    if productive_agents == 0:
        return 0
    return math.ceil(productive_agents / (1 - residual_shrinkage) - 1e-9)

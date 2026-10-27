"""Pure MUD combat formulas: hit chance, evasion, recovery, and reward shares.

Accuracy and evasion decide whether an attack connects; mitigation (defense)
only reduces the damage of attacks that do. Keeping the formulas here lets
tests and documentation check them without a database.
"""
import math

BASE_HIT = 85
MIN_HIT = 30
MAX_HIT = 95
EVASION_SOFT_CAP = 20
SPELL_ACCURACY_BONUS = 10
DEFEND_EVASION = 15
ITEM_EVASION_LIMIT = 20

REST_COOLDOWN_SECONDS = 30
GUARD_SECONDS = 30
REST_PERCENT = {"traditional": (25, 40), "procedural": (15, 25)}
DEATH_RECOVERY_PERCENT = 50
DEATH_EXPERIENCE_PERCENT = 10


def _clamp(low, high, value):
    return max(low, min(high, value))


def effective_evasion(evasion):
    """Evasion beyond the soft cap counts half, so stacking it has diminishing returns."""
    if evasion <= EVASION_SOFT_CAP:
        return evasion
    return EVASION_SOFT_CAP + (evasion - EVASION_SOFT_CAP) // 2


def hit_chance(accuracy, evasion):
    """Percent chance that an attack connects, never below MIN_HIT or above MAX_HIT."""
    return _clamp(MIN_HIT, MAX_HIT, BASE_HIT + accuracy - effective_evasion(evasion))


def resolve(accuracy, evasion, roll):
    """Classify a 0-99 roll as a hit, a dodge (evasion turned it), or a plain miss."""
    if roll < hit_chance(accuracy, evasion):
        return "hit"
    if roll < hit_chance(accuracy, 0):
        return "dodge"
    return "miss"


def recovery_amount(maximum, percent):
    return max(1, maximum * percent // 100)


def death_experience_loss(experience, level_floor):
    """Lose a share of progress into the current level; a level is never lost."""
    return max(0, experience - level_floor) * DEATH_EXPERIENCE_PERCENT // 100


def reward_threshold(contributors):
    """Minimum contribution share needed to earn rewards from a shared kill."""
    return min(0.10, 0.5 / max(1, contributors))


def reward_multiplier(share):
    """Solo kills pay in full; shared kills pay each contributor by their share."""
    return 0.4 + 0.6 * share


def scaled_reward(amount, share):
    if amount <= 0:
        return 0
    return max(1, math.ceil(amount * reward_multiplier(share) - 1e-9))

"""Central configuration constants for news_agent.

Rating weights control how per-axis signals combine into a composite score.
Weights should sum to approximately 1.0 for interpretability.
"""

# --- Rating composite weights ---
# quality_low: inverted (1 - quality_low), so near-0 = high-quality signal
RATING_WEIGHTS = {
    "quality_low": 0.2,  # inverted: (1 - quality_low) * 0.2
    "on_topic": 0.3,
    "importance": 0.3,
    "bt_z": 0.2,         # z-scored Bradley-Terry score
}

# Additive bonuses applied on top of the weighted composite
RECENCY_BONUS_WEIGHT = 0.1    # exp(-hours_since/48) * this weight
SITE_REPUTATION_BONUS_WEIGHT = 0.05  # site.reputation * this weight (Phase 3: always 0)

"""Central configuration constants for newsagent.

Rating coefficients are additive (legacy do_rating.py:511 parity):

    rating = c_reputation   * reputation
           + c_adjusted_len * adjusted_len
           + c_on_topic     * on_topic
           + c_importance   * importance
           - c_quality_low  * quality_low
           + c_bt_z         * bt_z
           + c_recency      * recency_score

Each term is roughly unit-scale, so totals fall in ~[0, 8] for typical articles.
Tune individual coefficients to bias the rating without touching rate.py.
"""

# --- Rating composite coefficients (additive) ---
RATING_COEFFS = {
    "reputation": 1.0,    # sites.reputation column (manually seeded)
    "adjusted_len": 1.0,  # clip(log10(content_length) - 3, 0, 2)
    "on_topic": 1.0,      # 0..1 LLM confidence
    "importance": 1.0,    # 0..1 LLM confidence
    "quality_low": 1.0,   # 0..1 LLM confidence — subtracted as penalty
    "bt_z": 1.0,          # z-scored Bradley-Terry score
    "recency": 1.0,       # 2 * exp(-ln2 * age_days) - 1  in [-1, +1]
}

# Drop articles older than this many days (legacy do_rating.py:426).
MAX_ARTICLE_AGE_DAYS = 7

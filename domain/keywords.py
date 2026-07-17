"""Shared device-classification keyword sets.

Single source of truth for the relief / verification device keywords used by both
evidence.py (assurance classification) and loto.py (procedure phase grouping).
They previously diverged -- notably 'test_point' (evidence) vs 'test point'
(loto) -- so a test-point device counted as verification evidence but was dropped
from the LOTO verify bucket. Keywords are normalized (underscores) here; callers
must normalize the text they match against the same way.
"""

# Stored-energy relief devices (open to depressurize / drain before/after lockout).
RELIEF_KEYWORDS = {"bleed", "vent", "drain"}

# Verification / proof-of-zero-energy devices (read to confirm isolation).
VERIFY_KEYWORDS = {"gauge", "indicator", "test_point"}

# Instrument tag families that indicate a verification device (e.g. PI-100, PG-3).
VERIFY_TAG_PREFIXES = {"pi", "pg"}

# Everything that counts as "verification evidence" for assurance classification.
VERIFICATION_ENTITY_KEYWORDS = RELIEF_KEYWORDS | VERIFY_KEYWORDS

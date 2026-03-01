# PixityAI v3 — Expansion Engine
# Multi-day compression + expansion detection system
#
# Architecture:
#   1. Daily compression scanner  → candidate list (causal, EOD)
#   2. RS_5d filter               → directional bias
#   3. 1H breakout trigger        → execution signal
#   4. Multi-day hold logic       → trade state machine
#   5. Portfolio risk controller  → position sizing with Nifty bias
#
# Strategy ID: pixityAI_v3_expansion
# All v2 paths remain registered but are NOT called from here.

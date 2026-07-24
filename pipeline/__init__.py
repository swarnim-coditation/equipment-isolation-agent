"""Shared pipeline layer: stage helpers used by BOTH run.py and agent/tools.py.

Imports nothing from ``agent`` or ``run`` -- the dependency arrow points one way,
so the two runners cannot drift through this layer.
"""

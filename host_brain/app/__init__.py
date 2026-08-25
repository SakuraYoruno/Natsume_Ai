"""Host app package boundary.

Import configuration, startup, and route modules from their concrete paths so
loading one app surface does not eagerly initialize unrelated infrastructure.
"""

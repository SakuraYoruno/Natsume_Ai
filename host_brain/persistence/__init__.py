"""Canonical Host persistence adapters.

Production code imports concrete repository modules directly.  Keeping the
package root empty prevents it from becoming a dynamic service locator and
keeps repository dependencies explicit to Python and architecture scans.
"""

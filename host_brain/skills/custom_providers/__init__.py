"""Retired legacy Python package for custom Skill providers.

TASK-0490 deliberately prevents package import here.  Signed external bundles
must remain in the supply-chain quarantine/staging area and may only contribute
verified declarative metadata through the new adapter; copying Python into this
package must never restore the old in-process hot-import path.
"""

raise RuntimeError("SKILL_SUPPLY_CHAIN_LEGACY_PACKAGE_IMPORT_REJECTED")

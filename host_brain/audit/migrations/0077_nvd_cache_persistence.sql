-- AW-03: catalog-owned NVD CVE cache and synchronization state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS cve_cache (
    cve_id TEXT PRIMARY KEY,
    cpe TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    product TEXT NOT NULL DEFAULT '',
    cvss_score REAL NOT NULL DEFAULT 0.0,
    cvss_vector TEXT NOT NULL DEFAULT '',
    cvss_severity TEXT NOT NULL DEFAULT '',
    cwe TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    exploit_maturity TEXT NOT NULL DEFAULT '',
    attack_chain TEXT NOT NULL DEFAULT '',
    patch TEXT NOT NULL DEFAULT '',
    published_date TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cve_vendor ON cve_cache(vendor);
CREATE INDEX IF NOT EXISTS idx_cve_product ON cve_cache(product);
CREATE INDEX IF NOT EXISTS idx_cve_score ON cve_cache(cvss_score);
CREATE INDEX IF NOT EXISTS idx_cve_modified ON cve_cache(last_modified);
CREATE INDEX IF NOT EXISTS idx_cve_cpe ON cve_cache(cpe);
CREATE INDEX IF NOT EXISTS idx_cve_synced ON cve_cache(synced_at);

COMMIT;

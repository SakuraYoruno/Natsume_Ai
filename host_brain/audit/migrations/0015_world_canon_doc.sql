-- Migration 0015: world_canon_doc 绱㈠紩琛紙M9 World Canon 闂幆锛?-- 瀛樺偍 World Canon 鏂囦欢鎵弿鍚庣殑绱㈠紩蹇収锛屼緵 prompt_context 娉ㄥ叆鍜?drift 妫€娴嬩娇鐢ㄣ€?-- 骞傜瓑锛欳REATE TABLE IF NOT EXISTS + ON CONFLICT UPSERT銆?-- 鍏宠仈锛欴evSpec 搂19.1.2锛汧R-WCANON-002锛汿ASK-0112銆?
CREATE TABLE IF NOT EXISTS world_canon_doc (
    doc_id      TEXT    PRIMARY KEY,
    version     INTEGER NOT NULL,
    hash        TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK(status IN ('active', 'deprecated', 'draft')),
    source_path TEXT    NOT NULL,
    updated_ts  INTEGER NOT NULL
);

-- 鎸?status 杩囨护 active 鏉＄洰锛坈ontext_pack Top-K 娉ㄥ叆锛?CREATE INDEX IF NOT EXISTS idx_world_canon_doc_status
    ON world_canon_doc(status);

-- 鎸夋洿鏂版椂闂存帓搴忥紙drift 妫€娴?/ 鏈€杩戝彉鏇存煡璇級
CREATE INDEX IF NOT EXISTS idx_world_canon_doc_updated_ts
    ON world_canon_doc(updated_ts);


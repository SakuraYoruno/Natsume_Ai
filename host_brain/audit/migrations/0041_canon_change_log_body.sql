-- Migration 0041: canon_change_log 增加 before_body / after_body 字段
-- 用于支持 rollback_proposal 时恢复原始文档内容，而非写入占位字符串。

ALTER TABLE canon_change_log ADD COLUMN before_body TEXT;
ALTER TABLE canon_change_log ADD COLUMN after_body  TEXT;

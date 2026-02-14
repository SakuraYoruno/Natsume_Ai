受众：MASTER

标题：需求可追溯性矩阵（Requirements Trace）

| ReqID   | 需求                                                  | 来源                 | 开发规格落点   | 验收测试            | 不变量    |
|:--------|:------------------------------------------------------|:---------------------|:---------------|:--------------------|:----------|
| R-01    | 单向链执行：Dify→Governor→Skills→Audit                | whitepaper 5/12/17   | DevSpec 0/6/8  | T-14,T-15,T-34      | I1,I8     |
| R-02    | 统一拒绝码 reason_code 且可统计                       | whitepaper 6.4/6.5   | DevSpec 4/9    | T-16,T-17,T-14      | (约束)    |
| R-03    | Intent 宽松解析 + 严格校验 + 重放保护                 | whitepaper 11.3/11.7 | DevSpec 3      | T-18~T-22           | I7        |
| R-04    | 票据化执行：approval/confirm/undo                     | whitepaper 12/25     | DevSpec 5      | T-34~T-38,T-55~T-58 | I1,I6,I11 |
| R-05    | ClawdBot 视觉熔断：红线/置信度/审计                   | whitepaper 8         | DevSpec 6.3    | T-41~T-43           | I4        |
| R-06    | SSOT：Neo4j Bond 仅 bond_dynamics 写；snapshot 仅派生 | whitepaper 10/13     | DevSpec 1/8    | T-50~T-54,T-74~T-75 | I9        |
| R-07    | 气闸：出站脱敏/入站隔离，不得直通执行                 | whitepaper 7         | DevSpec 8      | T-39,T-40           | I10,I3    |
| R-08    | QA：伪执行/Canon-as-fact/格式漂移拦截                 | whitepaper 26        | DevSpec 9      | T-67~T-71           | (约束)    |
| R-09    | Episodic 生命周期：证据链/衰减/合并/删除审计          | whitepaper 27        | DevSpec 8/10   | T-72~T-76           | I5        |


注意：此文档只做追溯，不写“请实现/请改代码”。

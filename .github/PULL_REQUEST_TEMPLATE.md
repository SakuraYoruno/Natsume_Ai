受众：PROJECT
标题：中文 Pull Request 模板

## 目标

<!-- 用中文说明本次 PR 要关闭的一个明确问题。 -->

## 生产接线

<!-- 列出真实入口、composition、持久 owner 和调用链；没有则明确写“无生产接线”。 -->

## 删除或停用的旧路径

<!-- 列出已删除/停用的旧 authority；没有则说明仍保留的边界。 -->

## 本地验证

<!-- 列出命令、结果、commit SHA 和 artifacts/local-gates/<sha>/ 下的证据。 -->

## 远程 CI 状态

<!-- 使用 PASS / FAIL / NOT_RUN / NOT_RUN_ACCOUNT_QUOTA，不能把零步骤额度失败写成 PASS。 -->

## 已知边界

<!-- 明确 PARTIAL / SHADOW / OFF / UNPROVEN / ACCEPTANCE_PENDING_EXTERNAL，不越级宣称完成。 -->

## 风险与回滚

<!-- 用中文说明风险、回滚提交或开关；没有则写“无”。 -->

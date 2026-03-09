# 实施计划：清理 fastapi_app.py 重复代码，确保单一职责

- [ ] 1. 确认各子模块的导出符号，补全缺失的公共导出
   - 检查 `schemas.py`、`dependencies.py`、`health_routes.py`、`prompt_builder.py`、`graph_config_builder.py` 是否已导出所有需要被 `fastapi_app.py` 引用的符号
   - 若某子模块缺少必要的 `__all__` 或公共函数导出，补充完整
   - _需求：1.2、2.3、3.3、4.2、5.2_

- [ ] 2. 删除 fastapi_app.py 中重复的 Schema 类，改为从 schemas.py 导入
   - 删除第 98–186 行的 10 个 Pydantic 模型类定义（`HealthResponse`、`ChatRequest`、`ChatResponse` 等）
   - 在文件顶部 import 区域添加 `from host_brain.app.schemas import HealthResponse, ReadinessResponse, ...`
   - 保留这些符号的重新导出，确保外部 `from fastapi_app import ChatRequest` 仍可用
   - _需求：1.1、1.2、1.3_

- [ ] 3. 删除 fastapi_app.py 中重复的场景常量和分类器，改为从 graph_config_builder.py 导入
   - 删除第 351–450 行的场景常量（`SCENE_CHAT`、`SCENE_WORK_CODE` 等）、关键词集合（`_PIC_*_KEYWORDS`）及 `pre_intent_classifier`、`_resolve_scene_node_sequence` 函数定义
   - 添加 `from host_brain.app.graph_config_builder import SCENE_CHAT, pre_intent_classifier, ...`
   - 保留这些符号的重新导出，确保测试文件中的导入不受影响
   - _需求：5.1、5.2、5.3_

- [ ] 4. 删除 fastapi_app.py 中重复的提示词工具函数，改为从 prompt_builder.py 导入
   - 删除第 509–870 行的 13 个文本处理函数定义（`_truncate_for_prompt`、`_render_host_generation_prompt` 等）
   - 同时删除第 2210 行的第二处 `_render_node_generation_prompt` 重复定义
   - 添加 `from host_brain.app.prompt_builder import _truncate_for_prompt, _render_node_generation_prompt, ...`
   - 确认文件中所有调用这些函数的代码均使用导入版本
   - _需求：4.1、4.2、4.3_

- [ ] 5. 删除 fastapi_app.py 中重复的依赖注入函数和全局单例变量，改为从 dependencies.py 导入
   - 删除第 1252–1530 行的 11 个全局单例变量（`_ledger`、`_intent_engine` 等）及 11 个工厂函数（`get_ledger`、`get_intent_engine` 等）定义
   - 添加 `from host_brain.app.dependencies import get_ledger, get_intent_engine, ...`
   - 更新 `lifespan` 函数中对这些函数的调用，确保使用导入版本
   - 保留这些符号的重新导出，确保 `obs_routes.py`、`web_ui.py` 及测试文件中的导入不受影响
   - _需求：2.1、2.2、2.3、2.4、2.5_

- [ ] 6. 删除 fastapi_app.py 中重复的健康检查路由，改为 include_router 注册
   - 删除第 1636–1864 行的 7 个路由处理函数（`health_check`、`environment_readiness` 等）及 `_resolve_node_inventory_payload`、`_load_json_file` 辅助函数定义
   - 在路由注册区域添加 `from host_brain.app.health_routes import router as health_router` 并调用 `app.include_router(health_router)`
   - 确认 `GET /health`、`GET /ready`、`GET /obs/metrics` 等 7 个端点路径前缀与原有路由一致
   - _需求：3.1、3.2、3.3、3.4_

- [ ] 7. 验证向后兼容性并整理最终导入结构
   - 梳理所有外部文件（`__main__.py`、`obs_routes.py`、`web_ui.py`、23 个测试文件）中从 `fastapi_app` 导入的符号清单
   - 确认 `fastapi_app.py` 顶部的重新导出语句覆盖了所有被外部引用的公共符号
   - 检查清理后文件大小是否降至 30KB 以内，确认无遗留重复代码块
   - _需求：6.1、6.2、6.3、6.4、6.5_

"""规范 Task Runtime 灰度契约的兼容导出。

与授权无关的契约位于 :mod:`host_brain.contracts`，从而使持久化层不反向依赖应用
运行时层。本模块仅为既有调用方保留身份不变的导入路径，不承载灰度判断或状态。
"""

from host_brain.contracts.task_runtime_feature_canary import *
from host_brain.contracts.task_runtime_feature_canary import __all__

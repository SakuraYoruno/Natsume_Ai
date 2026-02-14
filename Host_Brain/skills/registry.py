import functools
import inspect
from typing import Callable, Dict, Any

# 全局技能注册表
SKILL_REGISTRY: Dict[str, Callable] = {}


def register_skill(description: str):
    """
    装饰器：将函数注册为 Dify 可调用的 Skill
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # 注册
        SKILL_REGISTRY[func.__name__] = func
        return wrapper

    return decorator
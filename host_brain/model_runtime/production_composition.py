"""Production composition for the single model-attempt authority."""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.composition import (
    ModelRuntimeComposition,
    build_model_runtime_composition,
)
from host_brain.model_runtime.live_prompts import build_live_production_prompt_registry
from host_brain.model_runtime.production_provider_adapters import (
    BaseUrlResolver,
    CredentialResolver,
    SingleAttemptTransport,
    build_vault_backed_provider_registry,
)
from host_brain.model_runtime.production_route_policy import (
    build_production_server_route_catalog,
    build_server_owned_production_route_policy,
)


_DEFAULT_ROUTE_CATALOG = build_production_server_route_catalog()
_DEFAULT_PROMPT_REGISTRY = build_live_production_prompt_registry()


def get_default_production_route_catalog():
    """Return the catalog shared by projections and the production route policy."""

    # 默认目录是进程内共享的只读语义基线；启动与诊断投影使用同一实例，避免同一版本
    # 因重复构造出现不一致的 prompt/scene 参数。
    return _DEFAULT_ROUTE_CATALOG


def get_default_production_prompt_registry():
    """Return the one code-side registry shared by boot and composition."""

    # 与路由目录配对的注册表同样由组合层持有，业务模块不得自行创建“相同名称”的
    # 提示词登记表来绕过版本、摘要和审计身份。
    return _DEFAULT_PROMPT_REGISTRY


def build_default_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
) -> ModelRuntimeComposition:
    """Build the sole production prompt/route/provider/service object graph."""

    # 默认构造用于统一生产组合：凭据/base URL 仍由注入的 vault 解析器控制，transport
    # 只获得每次已授权尝试的目标，调用方不会接触密钥或 provider 配置。
    route_catalog = get_default_production_route_catalog()
    return _build_production_model_runtime(
        ledger=ledger,
        credential_resolver=credential_resolver,
        base_url_resolver=base_url_resolver,
        transport=transport,
        prompt_registry=get_default_production_prompt_registry(),
        route_catalog=route_catalog,
    )


def build_app_local_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
) -> ModelRuntimeComposition:
    """Build a fresh prompt/route/provider/service graph for one Host epoch."""

    # App 本地 epoch 需要独立对象图，防止测试/热重载的可变依赖污染全局默认组合；
    # 语义内容仍从同一构造函数生成，保持部署版本一致。
    route_catalog = build_production_server_route_catalog()
    return _build_production_model_runtime(
        ledger=ledger,
        credential_resolver=credential_resolver,
        base_url_resolver=base_url_resolver,
        transport=transport,
        prompt_registry=build_live_production_prompt_registry(),
        route_catalog=route_catalog,
    )


def _build_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
    prompt_registry: Any,
    route_catalog: Any,
) -> ModelRuntimeComposition:
    # 此处是提示词、服务端路由、凭据受控适配器和账本的唯一汇合点。下游仅持有
    # ModelRuntimeComposition，不能替换其中任一 authority 后继续沿生产调用路径执行。
    return build_model_runtime_composition(
        prompt_registry=prompt_registry,
        route_policy=build_server_owned_production_route_policy(
            route_catalog=route_catalog,
        ),
        route_catalog=route_catalog,
        provider_adapters=build_vault_backed_provider_registry(
            credential_resolver=credential_resolver,
            base_url_resolver=base_url_resolver,
            transport=transport,
        ),
        ledger=ledger,
    )


__all__ = [
    "build_app_local_production_model_runtime",
    "build_default_production_model_runtime",
    "get_default_production_prompt_registry",
    "get_default_production_route_catalog",
]

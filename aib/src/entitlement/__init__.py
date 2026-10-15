# SPDX-License-Identifier: Apache-2.0
"""付費資格與人格來源開關 API（§108.320 + 今晚包 D / DESIGN §4.4）。

for_task: §108.320 / evening-D-0926
與 license_plan / feature_policy 並列不混；不推 main。

查詢：
  get_entitlement(env=None) → dict(tier, providers, reason, source_path)
來源（給 C）：
  get_enabled_sources(env=None) → list[str]
  is_source_enabled(provider, env=None) → bool
"""
from .tier import TIERS, get_entitlement, verify_signature
from .store import resolve_home, resolve_license_path, read_license_obj
from .providers import (
    KNOWN_PROVIDERS,
    list_providers,
    parse_providers_from_launch,
)
from .sources import (
    get_enabled_sources,
    is_source_enabled,
    set_source,
    format_status,
    resolve_sources_path,
    read_sources_obj,
    allowed_providers_for_entitlement,
)

__all__ = [
    "TIERS",
    "get_entitlement",
    "verify_signature",
    "resolve_home",
    "resolve_license_path",
    "read_license_obj",
    "KNOWN_PROVIDERS",
    "list_providers",
    "parse_providers_from_launch",
    "get_enabled_sources",
    "is_source_enabled",
    "set_source",
    "format_status",
    "resolve_sources_path",
    "read_sources_obj",
    "allowed_providers_for_entitlement",
]

"""数据采集模块 - 各平台采集器统一接口"""

from .base import BaseCollector, RawComment

__all__ = ["BaseCollector", "RawComment"]
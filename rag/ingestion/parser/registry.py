"""
解析器注册表：(MIME × 解析档位) → 解析器，启动期建表，同一键被两个解析器认领即启动失败

查找顺序四档，全不命中显式报错，不静默兜底到某个通用解析器：
    精确 + 请求档位 → 通配 + 请求档位 → 精确 + FAST → 通配 + FAST → 抛错
FAST 是全局兜底档，解析器只需在有专属实现的档位显式声明。

selfCheck 因 Python 无 Tika，用「扩展名 → Tika 探测 MIME」的等价映射表做自检，
校验每个对外声称支持的扩展名都必须被某个解析器精准确认领。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.registry.ParserRegistry
"""
from typing import Dict, List, Optional, Set, Tuple

from rag.ingestion.parser.base import DocumentParser, ParseProfile

WILDCARD_SUFFIX = "/*"

# 启动自检清单：对外声明支持的扩展名，每个都必须被某个解析器在 FAST 档精准确认领。
# 清单落在扩展名而非 MIME 上，因为 MIME 是探测器的产出，拿它校验它自己恒为真。
# P1 3.6：随 Csv/Excel/Image 解析器就绪追加对应格式；pdf/doc 等复杂格式待 MinerU 接入后追加。
SUPPORTED_EXTENSIONS: Set[str] = {
    "md", "markdown", "txt", "text",
    "html", "htm", "json", "xml", "rtf",
    "csv",
    "xls", "xlsx",
    "png", "jpg", "jpeg", "svg",
}

# 扩展名 → Tika 探测 MIME 的等价映射（对应 Java Tika.detect("probe." + extension)）
_EXTENSION_TO_MIME: Dict[str, str] = {
    "md": "text/x-web-markdown",
    "markdown": "text/x-web-markdown",
    "txt": "text/plain",
    "text": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "json": "application/json",
    "xml": "application/xml",
    "rtf": "application/rtf",
    "csv": "text/csv",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}


def detect_mime(extension: str) -> str:
    """按扩展名探测 MIME（对应 Java Tika.detect("probe." + extension)），未知名返回空串"""
    return _EXTENSION_TO_MIME.get(extension, "")


class ParserRegistry:
    """
    解析器注册表：(MIME × 解析档位) → 解析器

    构造期遍历所有解析器的 supported_mime_types() 建表，同一路由键被两个解析器认领即启动失败。
    查找顺序：精确+请求档 → 通配+请求档 → 精确+FAST → 通配+FAST，全不命中显式报错。
    """

    def __init__(self, parsers: List[DocumentParser]):
        # 精确表：档位 → (全量 MIME → 解析器)
        self._exact: Dict[ParseProfile, Dict[str, DocumentParser]] = {}
        # 通配表：档位 → (MIME 大类前缀 → 解析器)，如 "text" → TextDocumentParser
        self._wildcard: Dict[ParseProfile, Dict[str, DocumentParser]] = {}
        for parser in parsers:
            claims = parser.supported_mime_types()
            if not claims:
                raise ValueError(f"解析器未声明任何 (MIME × 档位) 认领：{parser.parser_type}")
            for profile, mime_types in claims.items():
                self._register(parser, profile, mime_types)

    def _register(
        self,
        parser: DocumentParser,
        profile: ParseProfile,
        mime_types: Set[str],
    ) -> None:
        if profile is None or not mime_types:
            raise ValueError(f"解析器认领清单存在空档位或空 MIME 集合：{parser.parser_type}")
        for raw in mime_types:
            if not raw or not raw.strip():
                raise ValueError(f"解析器认领了空 MIME：{parser.parser_type}")
            mime = raw.strip().lower()
            is_wildcard = mime.endswith(WILDCARD_SUFFIX)
            key = mime[: -len(WILDCARD_SUFFIX)] if is_wildcard else mime
            table = (self._wildcard if is_wildcard else self._exact).setdefault(profile, {})
            previous = table.get(key)
            if previous is not None and previous is not parser:
                raise ValueError(
                    f"解析器路由键冲突：档位={profile.value} MIME={raw} "
                    f"同时被 {previous.parser_type} 与 {parser.parser_type} 认领，请显式区分"
                )
            table[key] = parser

    def self_check(self) -> None:
        """启动自检：对外声称支持的扩展名必须被 FAST 档精准确认领，非兜底档不得用通配认领"""
        exact_fallback = self._exact.get(ParseProfile.FAST, {})
        unclaimed = [
            f".{ext} → {detect_mime(ext)}"
            for ext in sorted(SUPPORTED_EXTENSIONS)
            if detect_mime(ext) not in exact_fallback
        ]
        if unclaimed:
            raise ValueError(
                "解析器注册表自检失败，以下扩展名探测出的 MIME 无人精准确认领，"
                f"只会落到通配兜底：{', '.join(unclaimed)}"
            )
        # 非兜底档只认精确 MIME：通配认领没法枚举成具体格式，档位差异会对外不可见
        illegal = [
            f"{profile.value} → {prefix}{WILDCARD_SUFFIX}"
            for profile, table in self._wildcard.items()
            if profile != ParseProfile.FAST
            for prefix in table
        ]
        if illegal:
            raise ValueError(
                "非兜底档只允许精确 MIME 认领，以下通配认领无法枚举成具体格式："
                f"{', '.join(illegal)}"
            )

    def find(self, mime_type: Optional[str], profile: Optional[ParseProfile] = None) -> Optional[DocumentParser]:
        """
        按 (MIME × 档位) 查找解析器

        Args:
            mime_type: 真实 MIME，允许带 ;charset= 参数
            profile: 请求档位，为空按默认档

        Returns:
            Optional[DocumentParser]: 命中解析器，未命中返回 None
        """
        mime = normalize(mime_type)
        if mime is None:
            return None
        requested = profile or ParseProfile.default_profile()
        hit = self._lookup(mime, requested)
        if hit is None and requested != ParseProfile.FAST:
            hit = self._lookup(mime, ParseProfile.FAST)
        return hit

    def require(self, mime_type: Optional[str], profile: Optional[ParseProfile] = None) -> DocumentParser:
        """
        按 (MIME × 档位) 查找解析器，认不出来就报错，不塞给某个通用解析器产出垃圾文本

        Raises:
            ValueError: 未找到对应解析器
        """
        parser = self.find(mime_type, profile)
        if parser is None:
            requested = profile or ParseProfile.default_profile()
            raise ValueError(
                f"未找到 MIME [{mime_type}] 在档位 [{requested.value}] 下对应的解析器"
            )
        return parser

    def profile_sensitive_mime_types(self) -> Set[str]:
        """
        档位真正有区别的 MIME：某个非兜底档命中的解析器 ≠ 兜底档命中的解析器

        供上层决定「解析档位」这个选项该不该给用户看：两档命中同一解析器时档位是空操作。
        """
        sensitive: Set[str] = set()
        for profile in ParseProfile:
            if profile == ParseProfile.FAST:
                continue
            for mime, parser in self._exact.get(profile, {}).items():
                if self._lookup(mime, ParseProfile.FAST) is not parser:
                    sensitive.add(mime)
        return sensitive

    def can_parse(self, mime_type: Optional[str]) -> bool:
        """该 MIME 是否有任何档位可解析，供上传前置拦截使用"""
        mime = normalize(mime_type)
        if mime is None:
            return False
        return any(self._lookup(mime, profile) is not None for profile in ParseProfile)

    def _lookup(self, mime: str, profile: ParseProfile) -> Optional[DocumentParser]:
        """单档位查找：精确键优先于通配键"""
        hit = self._exact.get(profile, {}).get(mime)
        if hit is not None:
            return hit
        slash = mime.find("/")
        if slash <= 0:
            return None
        return self._wildcard.get(profile, {}).get(mime[:slash])

    def route_stats(self) -> Tuple[int, int]:
        """精确键 / 通配键数量，供日志输出"""
        return (
            sum(len(t) for t in self._exact.values()),
            sum(len(t) for t in self._wildcard.values()),
        )


def normalize(mime_type: Optional[str]) -> Optional[str]:
    """归一化 MIME：去空白、转小写、剥离 ;charset= 参数；空值返回 None"""
    if not mime_type or not mime_type.strip():
        return None
    normalized = mime_type.strip().lower()
    semicolon = normalized.find(";")
    if semicolon >= 0:
        normalized = normalized[:semicolon].strip()
    return normalized or None

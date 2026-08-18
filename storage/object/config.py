"""
对象存储配置（对应 ragent RagStorageProperties + StorageClientConfig 的配置侧）

与 rag.vector.type（pg/milvus）、rag.keyword.type（none/es）同构，通过 type 在
S3 兼容存储（rustfs / minio）与阿里云 OSS 间切换。所有知识库文档共用一个全局桶
kbBucket（私有，按 collectionName 目录隔离）；多模态资产落公共读桶 assetBucket。

客户端构造（对应 Java StorageClientConfig 的 S3Client / S3Presigner / OSS bean）由
实现侧（步骤 2/3）按 type 二选一装配，本类只承载配置。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.RagStorageProperties
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class S3Config:
    """S3 兼容存储配置（type=s3 生效；对应 Java RagStorageProperties.S3）"""

    endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    path_style: bool = True  # rustfs / minio 需强制 path-style 寻址
    public_url: str = ""  # 浏览器可直连的公开基址，内外网端点不同时配置

    def resolve_public_url(self) -> str:
        """公开基址：留空时回退 endpoint（对应 Java resolvePublicUrl）"""
        return self.public_url.strip() if self.public_url.strip() else self.endpoint


@dataclass
class OssConfig:
    """阿里云 OSS 配置（type=oss 生效；对应 Java RagStorageProperties.Oss）"""

    endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    region: str = ""
    public_url: str = ""  # 资产公开访问基址（虚拟主机式，已含 bucket 子域）


@dataclass
class RagStorageProperties:
    """对象存储配置（对应 Java @ConfigurationProperties(prefix="rag.storage")）"""

    type: str = "s3"  # 存储后端类型：s3（默认）/ oss
    kb_bucket: str = "ragent-sources"  # 全局知识库桶（私有，按 collectionName 目录隔离）
    asset_bucket: str = "ragent-assets"  # 多模态资产桶（公共读）
    s3: S3Config = field(default_factory=S3Config)
    oss: OssConfig = field(default_factory=OssConfig)

    @staticmethod
    def from_dict(mapping: Dict[str, Any]) -> "RagStorageProperties":
        """
        从配置 dict 构建（形如 {"type": "s3", "kb_bucket": ..., "s3": {...}, "oss": {...}}）

        缺省字段回退默认值；s3/oss 嵌套 dict 缺省回退空配置。
        """
        mapping = mapping or {}
        s3 = mapping.get("s3") or {}
        oss = mapping.get("oss") or {}
        return RagStorageProperties(
            type=str(mapping.get("type") or "s3"),
            kb_bucket=str(mapping.get("kb_bucket") or "ragent-sources"),
            asset_bucket=str(mapping.get("asset_bucket") or "ragent-assets"),
            s3=S3Config(
                endpoint=str(s3.get("endpoint") or ""),
                access_key=str(s3.get("access_key") or ""),
                secret_key=str(s3.get("secret_key") or ""),
                region=str(s3.get("region") or "us-east-1"),
                path_style=bool(s3.get("path_style", True)),
                public_url=str(s3.get("public_url") or ""),
            ),
            oss=OssConfig(
                endpoint=str(oss.get("endpoint") or ""),
                access_key=str(oss.get("access_key") or ""),
                secret_key=str(oss.get("secret_key") or ""),
                region=str(oss.get("region") or ""),
                public_url=str(oss.get("public_url") or ""),
            ),
        )

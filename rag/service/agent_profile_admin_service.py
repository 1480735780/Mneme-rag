# -*- coding: utf-8 -*-
"""
rag.service.agent_profile_admin_service - 智能体档案管理 service（对应 Java AgentProfileAdminServiceImpl）

域职责（M5 5.5）：
    - CRUD + 全局唯一 activate + prompts 槽位读写；
    - 校验（对齐 Java）：
      · 名称必填（trim 后非空）→「智能体名称不能为空」；全局唯一（排除自身）→「智能体名称已存在」；
      · 内置智能体不可编辑/删除 →「内置智能体不可编辑或删除，如需调整请复制一份新建」；
      · 删除时若正在激活 →「该智能体正在激活中，请先激活其他智能体再删除」；
      · 头像长度上限 32 →「头像标识过长」；
      · savePrompt：未知槽位 →「未知的提示词：{slotKey}」；必填占位符缺失 →
        「「{displayName}」缺少必需占位符：{...}」；
    - 写后清 AgentPromptCacheManager 缓存（delete/activate/save_prompt 使提示词解析回源，对齐 Java clearCache）；
    - 读路径（叠加回落）复用 DatabaseAgentPromptResolver（本层仅编辑态用 load_own_prompts / default）。

编排模式（mode）：由注入 `OrchestrationMode` 决定槽位生效集合（部署级决策，Java OrchestrationProperties；
由 AppSettings.orchestration_mode 经 wiring 回注——见 app/wiring.py，缺省 WORKFLOW）。

方案 B：本层输出 snake_case dict，camelCase 序列化由 controller 边界 pydantic VO（5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.AgentProfileAdminService / Impl
    - com.nageoffer.ai.ragent.rag.controller.vo.AgentProfileVO / AgentProfileListVO / AgentPromptConfigVO
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from common.exception.business import ClientException
from rag.dao.agent_dao import AgentPromptDao, AgentProfileDao
from rag.prompt.agent_resolver import AgentPromptResolver as _Resolver
from rag.prompt.builder import (
    DEFAULT_AGENT_PROMPTS,
    AgentPromptCacheManager,
    AgentPromptSlot,
    OrchestrationMode,
)

logger = logging.getLogger(__name__)

# 头像列宽上限（对齐 Java AVATAR_MAX_LENGTH = 32）
AVATAR_MAX_LENGTH = 32


class AgentProfileAdminService:
    """智能体档案管理服务（对应 Java AgentProfileAdminServiceImpl）"""

    def __init__(
        self,
        profile_dao: AgentProfileDao,
        prompt_dao: AgentPromptDao,
        resolver: Optional[_Resolver] = None,
        prompt_cache_manager: Optional[AgentPromptCacheManager] = None,
        mode: Optional[OrchestrationMode] = None,
    ):
        self._profile_dao = profile_dao
        self._prompt_dao = prompt_dao
        # 编辑态用 load_own_prompts / default；缺省 None 时提示词默认回落为 None（测试可注入 stub）
        self._resolver = resolver
        # 写后清缓存对象（须与读路径共享同一实例，对齐 Java AgentPromptCacheManager）
        self._prompt_cache_manager = prompt_cache_manager
        self._mode = mode or OrchestrationMode.WORKFLOW

    # ==================== 列表 ====================

    def list(self) -> Dict:
        """档案列表（内置优先 + createTime 升序）+ 编排模式 + 槽位覆盖率（对齐 Java list）"""
        agents = self._profile_dao.list()
        agents.sort(key=lambda each: (
            (0 if _flag(each.get("builtin")) else 1),  # 内置优先（对齐 Java !builtin 比较器）
            each.get("create_time") or "",
        ))
        configured = self._load_configured_slots()
        vo_list = []
        for each in agents:
            own = configured.get(each.get("id"), [])
            effective = sum(1 for s in own if s.is_effective_in(self._mode))
            vo_list.append({
                "id": each.get("id"),
                "name": each.get("name"),
                "description": each.get("description"),
                "avatar": each.get("avatar"),
                "builtin": _flag(each.get("builtin")),
                "active": _flag(each.get("active")),
                "effective_slots": effective,
                "inactive_slots": len(own) - effective,
                "create_time": each.get("create_time"),
                "update_time": each.get("update_time"),
            })
        return {
            "mode": self._mode.value,
            "effective_slot_total": len(AgentPromptSlot.effective_in(self._mode)),
            "agents": vo_list,
        }

    # ==================== create / update / delete ====================

    def create(
        self,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> str:
        """创建档案（builtin=0/active=0），返回主键 ID"""
        name = self._trimmed_name(name)
        self._assert_name_available(name, None)
        return self._profile_dao.create(
            name=name,
            description=_trim_to_none(description),
            avatar=self._trimmed_avatar(avatar),
            builtin=0,
        )

    def update(
        self,
        pid: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> None:
        """更新档案（内置不可编辑）。

        语义：对齐 Java updateProfile 为 **PUT 全量表单**——name 必传（空白抛「智能体名称不能为空」），
        description/avatar 缺失或空白会被置为 NULL（清空），非「仅刷传非空字段」。
        name 全局唯一（排除自身）。
        """
        self._must_load_editable(pid)
        name = self._trimmed_name(name)
        self._assert_name_available(name, pid)
        self._profile_dao.update(
            pid,
            name=name,
            description=_trim_to_none(description),
            avatar=self._trimmed_avatar(avatar),
        )

    def delete(self, pid: str) -> None:
        """删除档案（内置不可删；激活中不可删）；级联软删其全部提示词 + 清提示词缓存"""
        profile = self._must_load_editable(pid)
        if _flag(profile.get("active")):
            raise ClientException("该智能体正在激活中，请先激活其他智能体再删除")
        self._prompt_dao.delete_by_agent(pid)
        self._profile_dao.delete(pid)
        self._clear_prompt_cache()

    def activate(self, pid: str) -> None:
        """激活档案（全局仅一条 active=1，对齐 Java activate）；写后清提示词缓存"""
        self._must_load(pid)
        self._profile_dao.activate(pid)
        self._clear_prompt_cache()

    # ==================== 提示词槽位 ====================

    def load_prompts(self, pid: str) -> Dict:
        """槽位配置视图（对齐 Java loadPrompts：槽位元数据 + 自身内容，不回落）"""
        profile = self._must_load(pid)
        builtin = self._load_builtin()
        own = self._resolver.load_own_prompts(pid) if self._resolver else {}
        slots = []
        for slot in AgentPromptSlot:
            effective = slot.is_effective_in(self._mode)
            slots.append({
                "slot_key": slot.name,
                "display_name": slot.display_name,
                "group": slot.group.name,
                "group_name": slot.group.value,
                "effective": effective,
                "inactive_reason": None if effective else slot.inactive_reason,
                "required_placeholders": sorted(slot.required_placeholders),
                "content": own.get(slot.name, ""),
            })
        return {
            "agent_id": pid,
            "agent_name": profile.get("name"),
            "builtin": _flag(profile.get("builtin")),
            "default_agent_name": builtin.get("name") if builtin else None,
            "mode": self._mode.value,
            "slots": slots,
        }

    def save_prompt(self, pid: str, slot_key: str, content: Optional[str] = None) -> None:
        """保存槽位提示词（内置不可编辑；未知槽位/缺必需占位符拒绝；写后清提示词缓存）"""
        self._must_load_editable(pid)
        slot = AgentPromptSlot.find(slot_key)
        if slot is None:
            raise ClientException(f"未知的提示词：{slot_key}")
        self._assert_placeholders_present(slot, content)

        content_blank = _is_blank(content)
        blank_value = None if content_blank else content
        # 对齐 Java：已存在则真写 null（空白可清空回落）；不存在且空白则不落库
        if self._prompt_dao.find_by_agent_slot(pid, slot.name) is not None:
            self._prompt_dao.save(pid, slot.name, blank_value)
        elif not content_blank:
            self._prompt_dao.save(pid, slot.name, content)
        self._clear_prompt_cache()

    def default_prompt(self, slot_key: str) -> str:
        """槽位默认提示词（对齐 Java defaultPrompt）：内置智能体自身内容 → **内置默认模板** 兜底"""
        slot = AgentPromptSlot.find(slot_key)
        if slot is None:
            raise ClientException(f"未知的提示词：{slot_key}")
        builtin = self._load_builtin()
        own = ""
        if builtin is not None and self._resolver is not None:
            own = self._resolver.load_own_prompts(builtin.get("id")).get(slot.name, "") or ""
        # DB 内置智能体未配置该槽位 → 回落代码级内置默认（关闭「推荐问题默认模板」DoD gap）
        return own or DEFAULT_AGENT_PROMPTS.get(slot.name, "")

    # ==================== 内部辅助 ====================

    def _load_configured_slots(self) -> Dict[str, List[AgentPromptSlot]]:
        """按智能体聚合已配置槽位（仅非空 content），未知 slot_key 忽略（对齐 Java loadConfiguredSlots）"""
        configured: Dict[str, List[AgentPromptSlot]] = {}
        for profile in self._profile_dao.list():
            agent_id = profile.get("id")
            for prompt in self._prompt_dao.list_by_agent(agent_id):
                if not _is_blank(prompt.get("content")):
                    slot = AgentPromptSlot.find(prompt.get("slot_key"))
                    if slot is not None:
                        configured.setdefault(agent_id, []).append(slot)
        return configured

    def _load_builtin(self) -> Optional[Dict]:
        """内置智能体（builtin=1）；缺失返回 None（对应 Java loadBuiltin）"""
        for profile in self._profile_dao.list():
            if _flag(profile.get("builtin")):
                return profile
        return None

    def _must_load(self, pid: str) -> Dict:
        profile = self._profile_dao.find_by_id(pid)
        if profile is None:
            raise ClientException("智能体不存在")
        return profile

    def _must_load_editable(self, pid: str) -> Dict:
        profile = self._must_load(pid)
        if _flag(profile.get("builtin")):
            raise ClientException("内置智能体不可编辑或删除，如需调整请复制一份新建")
        return profile

    def _trimmed_name(self, name: Optional[str]) -> str:
        text = str(name).strip() if name is not None else ""
        if not text:
            raise ClientException("智能体名称不能为空")
        return text

    def _trimmed_avatar(self, avatar: Optional[str]) -> Optional[str]:
        avatar = _trim_to_none(avatar)
        if avatar is not None and len(avatar) > AVATAR_MAX_LENGTH:
            raise ClientException("头像标识过长")
        return avatar

    def _assert_name_available(self, name: str, exclude_id: Optional[str]) -> None:
        for profile in self._profile_dao.list():
            if profile.get("name") == name and profile.get("id") != exclude_id:
                raise ClientException("智能体名称已存在")

    @staticmethod
    def _assert_placeholders_present(slot: AgentPromptSlot, content: Optional[str]) -> None:
        """必需占位符缺失会让下游规则静默失效，保存时即拒绝（对齐 Java assertPlaceholdersPresent）"""
        if _is_blank(content) or not slot.required_placeholders:
            return
        missing = sorted(p for p in slot.required_placeholders if p not in (content or ""))
        if missing:
            raise ClientException(f"「{slot.display_name}」缺少必需占位符：{'、'.join(missing)}")

    def _clear_prompt_cache(self) -> None:
        """写后清提示词解析缓存（对齐 Java cacheManager.clearCache）；失败仅告警"""
        if self._prompt_cache_manager is None:
            return
        try:
            self._prompt_cache_manager.clear_cache()
        except Exception:  # noqa: BLE001 —— 清理失败仅告警，不阻断写操作
            logger.warning("智能体提示词缓存清理失败", exc_info=True)


# ==================== 工具 ====================


def _flag(value) -> bool:
    return value is not None and value == 1


def _is_blank(value: Optional[str]) -> bool:
    return value is None or not str(value).strip()


def _trim_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
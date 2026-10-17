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
    - 写后清 AgentPromptCacheManager 缓存（delete/activate 使提示词解析回源，对齐 Java clearCache；
      save_prompt 自 P10.2 T2.2 起语义 = 新建版本行、不改生效内容，故不再清缓存）；
    - 读路径（叠加回落）复用 DatabaseAgentPromptResolver（本层仅编辑态用 load_own_prompts / default）；
    - P10.2 T2.2 版本化（D8）：保存 = 新不可变版本行（永不 UPDATE 内容）；激活/回滚 =
      diff 预览 + 二次确认 + 切 t_agent_prompt.active_version_id 指针（回滚同路径无特例），
      审计经 @record_biz_change 落 t_biz_change_log（ACTIVATE/ROLLBACK 按操作类型区分）。

编排模式（mode）：由注入 `OrchestrationMode` 决定槽位生效集合（部署级决策，Java OrchestrationProperties；
由 AppSettings.orchestration_mode 经 wiring 回注——见 app/wiring.py，缺省 WORKFLOW）。

方案 B：本层输出 snake_case dict，camelCase 序列化由 controller 边界 pydantic VO（5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.AgentProfileAdminService / Impl
    - com.nageoffer.ai.ragent.rag.controller.vo.AgentProfileVO / AgentProfileListVO / AgentPromptConfigVO
"""

from __future__ import annotations

import difflib
import logging
from typing import Dict, List, Optional

from audit.support.context import BizChangeLogContext
from audit.support.decorator import record_biz_change
from common.exception.business import ClientException
from rag.dao.agent_dao import AgentPromptDao, AgentProfileDao, AgentPromptVersionDao
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
        prompt_version_dao: Optional[AgentPromptVersionDao] = None,
    ):
        self._profile_dao = profile_dao
        self._prompt_dao = prompt_dao
        # 编辑态用 load_own_prompts / default；缺省 None 时提示词默认回落为 None（测试可注入 stub）
        self._resolver = resolver
        # 写后清缓存对象（须与读路径共享同一实例，对齐 Java AgentPromptCacheManager）
        self._prompt_cache_manager = prompt_cache_manager
        self._mode = mode or OrchestrationMode.WORKFLOW
        # P10.2 T2.2（D8）：提示词版本数据访问；未装配时版本方法显式报错（老用例不受影响）
        self._prompt_version_dao = prompt_version_dao

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
        """删除档案（内置不可删；激活中不可删）；级联软删其全部提示词

        P10.2 T2.3（D9）：不再手动清缓存——被删方既非内置也非激活，不参与运行时
        解析；指纹机制（版本号入 key）正常链路无需手动失效。
        """
        profile = self._must_load_editable(pid)
        if _flag(profile.get("active")):
            raise ClientException("该智能体正在激活中，请先激活其他智能体再删除")
        self._prompt_dao.delete_by_agent(pid)
        self._profile_dao.delete(pid)

    def activate(self, pid: str) -> None:
        """激活档案（全局仅一条 active=1，对齐 Java activate）

        P10.2 T2.3（D9）：不再手动清缓存——激活换人即指针指纹（含 active_id）必变，
        缓存 key 必然 miss，改动 ≤5s（指纹 TTL）自然生效。
        """
        self._must_load(pid)
        self._profile_dao.activate(pid)

    # ==================== 提示词槽位 ====================

    def load_prompts(self, pid: str) -> Dict:
        """槽位配置视图（对齐 Java loadPrompts：槽位元数据 + 自身生效内容，不回落）"""
        profile = self._must_load(pid)
        builtin = self._load_builtin()
        slots = []
        for slot in AgentPromptSlot:
            manifest = self._effective_prompt(pid, slot.name)
            effective = slot.is_effective_in(self._mode)
            slots.append({
                "slot_key": slot.name,
                "display_name": slot.display_name,
                "group": slot.group.name,
                "group_name": slot.group.value,
                "effective": effective,
                "inactive_reason": None if effective else slot.inactive_reason,
                "required_placeholders": sorted(slot.required_placeholders),
                "content": manifest.get("content") or "",
                "content_source": manifest["source"],
            })
        return {
            "agent_id": pid,
            "agent_name": profile.get("name"),
            "builtin": _flag(profile.get("builtin")),
            "default_agent_name": builtin.get("name") if builtin else None,
            "mode": self._mode.value,
            "slots": slots,
        }

    def save_prompt(self, pid: str, slot_key: str, content: Optional[str] = None) -> Dict:
        """保存槽位提示词 = 新建不可变版本行（内置不可编辑；未知槽位/缺必需占位符拒绝）

        P10.2 T2.2（D8）：保存 ≠ 生效——版本行只入 t_agent_prompt_version（content
        恒不覆盖既有生效内容、不清缓存：生效由 activate/rollback 显式切指针）。
        空白内容同样成版本行（content=None），激活后恢复「未配置回落」语义。
        版本行本身即保存审计（create_by/create_time 随行）。

        Returns:
            {"agent_id", "slot_key", "version_id", "version_no"} 版本行摘要
        """
        self._must_load_editable(pid)
        slot = self._must_slot(slot_key)
        self._assert_placeholders_present(slot, content)

        blank_value = None if _is_blank(content) else content
        version = self._must_version_dao().save_version(pid, slot.name, blank_value)
        return {
            "agent_id": pid,
            "slot_key": slot.name,
            "version_id": version["id"],
            "version_no": version["version_no"],
        }

    # ==================== 提示词版本化（P10.2 T2.2，D8：保存≠生效） ====================

    def list_prompt_versions(self, pid: str, slot_key: str, limit: int = 50) -> Dict:
        """版本历史视图：版本行列表 + 当前生效指针与生效来源（编辑态/激活确认共用数据源）"""
        self._must_load(pid)
        slot = self._must_slot(slot_key)
        effective = self._effective_prompt(pid, slot.name)
        versions = [
            {
                "version_id": each["id"],
                "version_no": each.get("version_no"),
                "content": each.get("content"),
                "create_by": each.get("create_by"),
                "create_time": each.get("create_time"),
                "is_active": each["id"] == effective.get("version_id"),
            }
            for each in self._must_version_dao().list_by_slot(pid, slot.name, limit=limit)
        ]
        return {
            "agent_id": pid,
            "slot_key": slot.name,
            "active_source": effective["source"],
            "active_version_id": effective.get("version_id"),
            "versions": versions,
        }

    def preview_prompt_version(self, pid: str, slot_key: str, version_id: str) -> Dict:
        """激活/回滚前 diff 预览（D8：当前生效 vs 目标版本，unified diff 行 + changed 标志）"""
        self._must_load(pid)
        slot = self._must_slot(slot_key)
        target = self._must_version(pid, slot, version_id)
        current = self._effective_prompt(pid, slot.name)
        from_text = current.get("content") or ""
        to_text = target.get("content") or ""
        diff_lines = list(
            difflib.unified_diff(
                from_text.splitlines(),
                to_text.splitlines(),
                fromfile=f"当前生效({current['source']})",
                tofile=f"目标版本 v{target.get('version_no')}",
                lineterm="",
            )
        )
        return {
            "agent_id": pid,
            "slot_key": slot.name,
            "slot_display_name": slot.display_name,
            "current": {
                "source": current["source"],
                "version_id": current.get("version_id"),
                "version_no": current.get("version_no"),
                "content": current.get("content"),
            },
            "target": {
                "version_id": target["id"],
                "version_no": target.get("version_no"),
                "content": target.get("content"),
            },
            "changed": bool(diff_lines),
            "unified_diff": diff_lines,
        }

    @record_biz_change("AGENT_PROMPT", "ACTIVATE_PROMPT_VERSION", "激活提示词版本")
    def activate_prompt_version(
        self, pid: str, slot_key: str, version_id: str, *, confirm: bool = False
    ) -> Dict:
        """激活版本：diff 二次确认（confirm=True）→ 切 t_agent_prompt.active_version_id → 审计"""
        return self._switch_prompt_pointer(pid, slot_key, version_id, confirm=confirm)

    @record_biz_change("AGENT_PROMPT", "ROLLBACK_PROMPT_VERSION", "回滚提示词版本")
    def rollback_prompt_version(
        self, pid: str, slot_key: str, version_id: str, *, confirm: bool = False
    ) -> Dict:
        """回滚 = 切指针同路径无特例（D8）：语义与激活一致，仅审计 operation_type 区分意图"""
        return self._switch_prompt_pointer(pid, slot_key, version_id, confirm=confirm)

    def _switch_prompt_pointer(
        self, pid: str, slot_key: str, version_id: str, *, confirm: bool
    ) -> Dict:
        """激活/回滚共用核心（D8 回滚无特例）：校验 → 二次确认 → 切指针 → 快照入审计上下文"""
        self._must_load_editable(pid)
        slot = self._must_slot(slot_key)
        target = self._must_version(pid, slot, version_id)
        if not confirm:
            raise ClientException("激活需二次确认：请先调用版本预览查看 diff，再以 confirm=true 提交")
        current = self._effective_prompt(pid, slot.name)
        before = {
            "source": current["source"],
            "version_id": current.get("version_id"),
            "content": current.get("content"),
        }
        self._prompt_dao.set_active_version(pid, slot.name, version_id)
        after = {
            "source": "pointer",
            "version_id": version_id,
            "version_no": target.get("version_no"),
        }
        BizChangeLogContext().put(f"{pid}:{slot.name}", before, after)
        return {
            "agent_id": pid,
            "slot_key": slot.name,
            "version_id": version_id,
            "version_no": target.get("version_no"),
        }

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
        """按智能体聚合已配置槽位（指针版本或存量 content 任一非空即计入），未知 slot_key 忽略"""
        configured: Dict[str, List[AgentPromptSlot]] = {}
        for profile in self._profile_dao.list():
            agent_id = profile.get("id")
            for prompt in self._prompt_dao.list_by_agent(agent_id):
                slot = AgentPromptSlot.find(prompt.get("slot_key"))
                if slot is not None:
                    content = self._effective_prompt_from_row(prompt).get("content")
                    if not _is_blank(content):
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

    def _must_slot(self, slot_key: str) -> AgentPromptSlot:
        slot = AgentPromptSlot.find(slot_key)
        if slot is None:
            raise ClientException(f"未知的提示词：{slot_key}")
        return slot

    def _must_version_dao(self) -> AgentPromptVersionDao:
        if self._prompt_version_dao is None:
            raise ClientException("提示词版本化未装配（AgentPromptVersionDao 缺失）")
        return self._prompt_version_dao

    def _must_version(self, pid: str, slot: AgentPromptSlot, version_id: str) -> Dict:
        """版本存在且归属当前智能体当前槽位（防跨槽/跨智能体误切指针）"""
        version = self._must_version_dao().get_by_id(version_id)
        if version is None or version.get("agent_id") != pid or version.get("slot_key") != slot.name:
            raise ClientException("提示词版本不存在")
        return version

    def _effective_prompt(self, agent_id: str, slot_name: str) -> Dict:
        """槽位生效内容三态来源（D8 读侧约定）：pointer 版本内容 > 未版本化存量 content > 未配置"""
        row = self._prompt_dao.find_by_agent_slot(agent_id, slot_name)
        return self._effective_prompt_from_row(row)

    def _effective_prompt_from_row(self, row: Optional[Dict]) -> Dict:
        """按已查得的槽位行判生效来源（避免 list 场景 N+1 重复查询）"""
        if not row:
            return {"source": "unconfigured", "content": None, "version_id": None, "version_no": None}
        pointer = row.get("active_version_id")
        if pointer and self._prompt_version_dao is not None:
            version = self._prompt_version_dao.get_by_id(pointer)
            if version is not None:
                return {
                    "source": "pointer",
                    "content": version.get("content"),
                    "version_id": pointer,
                    "version_no": version.get("version_no"),
                }
        return {
            "source": "slot",
            "content": row.get("content"),
            "version_id": None,
            "version_no": None,
        }

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
        """清提示词解析缓存（运维逃生舱；失败仅告警）

        P10.2 T2.3（D9）起 deprecated：正常链路已不依赖手动失效（版本号入缓存 key，
        激活/回滚切指针后指纹必变、key 自然 miss），本方法保留给显式运维干预场景。
        """
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
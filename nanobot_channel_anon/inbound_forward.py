"""入站 forward 展开逻辑."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.domain import (
    ForwardExpanded,
    ForwardNode,
    ForwardRef,
    NormalizedMessage,
)


class ForwardMessageFetcher(Protocol):
    """支持拉取合并转发原始载荷的依赖接口."""

    async def get_forward_message(self, forward_id: str) -> Any | None:
        """按 forward_id 拉取合并转发原始载荷."""


class InboundForwardEnricher:
    """补齐入站消息里的单层 forward 展开."""

    def __init__(self, *, fetcher: ForwardMessageFetcher) -> None:
        """保存转发拉取依赖."""
        self._fetcher = fetcher

    async def enrich(self, message: NormalizedMessage) -> NormalizedMessage:
        """为消息写入 presenter 可消费的 forward_expanded 元数据."""
        if message.message_type != "message":
            return message
        forward_refs = self._forward_refs_from_metadata(message.metadata)
        if not forward_refs:
            return message

        expanded_items: list[dict[str, Any] | None] = []
        has_expanded = False
        for ref in forward_refs:
            expanded = await self._expand_forward_ref(ref)
            if expanded is None:
                expanded_items.append(None)
                continue
            has_expanded = True
            expanded_items.append(expanded.model_dump(exclude_none=True))
        if not has_expanded:
            return message

        metadata = dict(message.metadata)
        metadata["forward_expanded"] = expanded_items
        return message.model_copy(update={"metadata": metadata})

    async def _expand_forward_ref(self, ref: ForwardRef) -> ForwardExpanded | None:
        if ref.forward_id:
            payload = await self._fetcher.get_forward_message(ref.forward_id)
            nodes = self._extract_forward_nodes(payload)
            if nodes:
                return ForwardExpanded(
                    forward_id=ref.forward_id,
                    summary=ref.summary,
                    nodes=nodes,
                )
            return None
        if ref.nodes:
            return ForwardExpanded(
                forward_id=None,
                summary=ref.summary,
                nodes=ref.nodes,
            )
        return None

    @staticmethod
    def _forward_refs_from_metadata(metadata: dict[str, object]) -> list[ForwardRef]:
        raw_items = metadata.get("forwards")
        if not isinstance(raw_items, list):
            return []
        refs: list[ForwardRef] = []
        for item in raw_items:
            if isinstance(item, ForwardRef):
                refs.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                refs.append(ForwardRef.model_validate(item))
            except ValidationError:
                continue
        return refs

    @staticmethod
    def _extract_forward_nodes(payload: Any) -> list[ForwardNode]:
        raw_nodes: list[dict[str, Any]]
        if isinstance(payload, list):
            raw_nodes = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            raw_nodes = []
            for key in ("messages", "message", "content"):
                value = payload.get(key)
                if isinstance(value, list):
                    raw_nodes = [item for item in value if isinstance(item, dict)]
                    break
        else:
            raw_nodes = []

        nodes: list[ForwardNode] = []
        for raw_node in raw_nodes:
            node = OneBotMapper.build_forward_node(raw_node)
            if not node.sender_id and not node.sender_name and not node.content:
                continue
            nodes.append(node)
        return nodes

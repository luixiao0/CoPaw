# -*- coding: utf-8 -*-
"""CoPaw Agent - Main agent implementation.

This module provides the main CoPawAgent class built on ReActAgent,
with integrated tools, skills, and memory management.
"""
import logging
import os
import asyncio
from typing import Any, List, Optional, Type

from agentscope.agent import ReActAgent
from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit
from pydantic import BaseModel

from .command_handler import CommandHandler
from .hooks import BootstrapHook, MemoryCompactionHook
from .memory import CoPawInMemoryMemory
from .model_capabilities import supports_input_capability
from .model_factory import (
    create_model_and_formatter,
    create_model_from_config,
    create_text_and_vlm_models,
)
from .image_understanding import (
    get_last_message,
    run_media_understanding_prepass,
)
from .vision_prepass import format_vlm_prepass_context
from .prompt import build_system_prompt_from_working_dir
from .skills_manager import (
    ensure_skills_initialized,
    get_working_skills_dir,
    list_available_skills,
)
from .tools import (
    browser_use,
    create_memory_search_tool,
    desktop_screenshot,
    edit_file,
    execute_shell_command,
    get_current_time,
    read_file,
    send_file_to_user,
    write_file,
)
from .utils import process_file_and_media_blocks_in_message
from ..agents.memory import MemoryManager
from ..config import load_config
from ..constant import (
    MEMORY_COMPACT_KEEP_RECENT,
    MEMORY_COMPACT_RATIO,
    WORKING_DIR,
)
from ..providers import (
    get_active_llm_config,
    get_active_vlm_config,
    get_active_vlm_fallback_configs,
    load_providers_json,
)

logger = logging.getLogger(__name__)
_MEDIA_CAPABILITIES_ORDER = ("image", "audio", "video")


class CoPawAgent(ReActAgent):
    """CoPaw Agent with integrated tools, skills, and memory management.

    This agent extends ReActAgent with:
    - Built-in tools (shell, file operations, browser, etc.)
    - Dynamic skill loading from working directory
    - Memory management with auto-compaction
    - Bootstrap guidance for first-time setup
    - System command handling (/compact, /new, etc.)
    """

    def __init__(
        self,
        env_context: Optional[str] = None,
        enable_memory_manager: bool = True,
        mcp_clients: Optional[List[Any]] = None,
        memory_manager: MemoryManager | None = None,
        max_iters: int = 50,
        max_input_length: int = 128 * 1024,  # 128K = 131072 tokens
    ):
        """Initialize CoPawAgent.

        Args:
            env_context: Optional environment context to prepend to
                system prompt
            enable_memory_manager: Whether to enable memory manager
            mcp_clients: Optional list of MCP clients for tool
                integration
            memory_manager: Optional memory manager instance
            max_iters: Maximum number of reasoning-acting iterations
                (default: 50)
            max_input_length: Maximum input length in tokens for model
                context window (default: 128K = 131072)
        """
        self._env_context = env_context
        self._max_input_length = max_input_length
        self._mcp_clients = mcp_clients or []

        # Memory compaction threshold: configurable ratio of max_input_length
        self._memory_compact_threshold = int(
            max_input_length * MEMORY_COMPACT_RATIO,
        )

        # Initialize toolkit with built-in tools
        toolkit = self._create_toolkit()

        # Load and register skills
        self._register_skills(toolkit)

        # Build system prompt
        sys_prompt = self._build_sys_prompt()

        # Create text model and optional VLM models for capability-aware routing.
        self._active_llm_cfg = get_active_llm_config()
        self._active_vlm_cfg = get_active_vlm_config()
        self._active_vlm_fallback_cfgs = get_active_vlm_fallback_configs()
        self._vision_settings = load_providers_json().vision

        if self._active_llm_cfg is not None:
            model, self._vlm_model, formatter = create_text_and_vlm_models(
                self._active_llm_cfg,
                self._active_vlm_cfg,
            )
        else:
            # Keep backward-compatible env fallback behavior.
            model, formatter = create_model_and_formatter()
            self._vlm_model = None
        self._vlm_fallback_models = []
        for cfg in self._active_vlm_fallback_cfgs:
            fallback_model, _ = create_model_from_config(cfg)
            self._vlm_fallback_models.append((cfg, fallback_model))

        # Initialize parent ReActAgent
        super().__init__(
            name="Friday",
            model=model,
            sys_prompt=sys_prompt,
            toolkit=toolkit,
            memory=CoPawInMemoryMemory(),
            formatter=formatter,
            max_iters=max_iters,
        )

        # Setup memory manager
        self._setup_memory_manager(
            enable_memory_manager,
            memory_manager,
        )

        # Setup command handler
        self.command_handler = CommandHandler(
            agent_name=self.name,
            memory=self.memory,
            formatter=self.formatter,
            memory_manager=self.memory_manager,
            enable_memory_manager=self._enable_memory_manager,
        )

        # Register hooks
        self._register_hooks()

    def _create_toolkit(self) -> Toolkit:
        """Create and populate toolkit with built-in tools.

        Returns:
            Configured toolkit instance
        """
        toolkit = Toolkit()

        # Register built-in tools
        toolkit.register_tool_function(execute_shell_command)
        toolkit.register_tool_function(read_file)
        toolkit.register_tool_function(write_file)
        toolkit.register_tool_function(edit_file)
        toolkit.register_tool_function(browser_use)
        toolkit.register_tool_function(desktop_screenshot)
        toolkit.register_tool_function(send_file_to_user)
        toolkit.register_tool_function(get_current_time)

        return toolkit

    def _register_skills(self, toolkit: Toolkit) -> None:
        """Load and register skills from working directory.

        Args:
            toolkit: Toolkit to register skills to
        """
        # Check skills initialization
        ensure_skills_initialized()

        working_skills_dir = get_working_skills_dir()
        available_skills = list_available_skills()

        for skill_name in available_skills:
            skill_dir = working_skills_dir / skill_name
            if skill_dir.exists():
                try:
                    toolkit.register_agent_skill(str(skill_dir))
                    logger.debug("Registered skill: %s", skill_name)
                except Exception as e:
                    logger.error(
                        "Failed to register skill '%s': %s",
                        skill_name,
                        e,
                    )

    def _build_sys_prompt(self) -> str:
        """Build system prompt from working dir files and env context.

        Returns:
            Complete system prompt string
        """
        sys_prompt = build_system_prompt_from_working_dir()
        if self._env_context is not None:
            sys_prompt = self._env_context + "\n\n" + sys_prompt
        return sys_prompt

    def _setup_memory_manager(
        self,
        enable_memory_manager: bool,
        memory_manager: MemoryManager | None,
    ) -> None:
        """Setup memory manager and register memory search tool if enabled.

        Args:
            enable_memory_manager: Whether to enable memory manager
            memory_manager: Optional memory manager instance
        """
        # Check env var: if ENABLE_MEMORY_MANAGER=false, disable memory manager
        env_enable_mm = os.getenv("ENABLE_MEMORY_MANAGER", "")
        if env_enable_mm.lower() == "false":
            enable_memory_manager = False

        self._enable_memory_manager: bool = enable_memory_manager
        self.memory_manager = memory_manager

        # Register memory_search tool if enabled and available
        if self._enable_memory_manager and self.memory_manager is not None:
            self.memory_manager.chat_model = self.model
            self.memory_manager.formatter = self.formatter

            memory_search_tool = create_memory_search_tool(self.memory_manager)
            self.toolkit.register_tool_function(memory_search_tool)
            logger.debug("Registered memory_search tool")

    def _register_hooks(self) -> None:
        """Register pre-reasoning hooks for bootstrap and memory compaction."""
        # Bootstrap hook - checks BOOTSTRAP.md on first interaction
        config = load_config()
        bootstrap_hook = BootstrapHook(
            working_dir=WORKING_DIR,
            language=config.agents.language,
        )
        self.register_instance_hook(
            hook_type="pre_reasoning",
            hook_name="bootstrap_hook",
            hook=bootstrap_hook.__call__,
        )
        logger.debug("Registered bootstrap hook")

        # Memory compaction hook - auto-compact when context is full
        if self._enable_memory_manager and self.memory_manager is not None:
            memory_compact_hook = MemoryCompactionHook(
                memory_manager=self.memory_manager,
                memory_compact_threshold=self._memory_compact_threshold,
                keep_recent=MEMORY_COMPACT_KEEP_RECENT,
            )
            self.register_instance_hook(
                hook_type="pre_reasoning",
                hook_name="memory_compact_hook",
                hook=memory_compact_hook.__call__,
            )
            logger.debug("Registered memory compaction hook")

    def rebuild_sys_prompt(self) -> None:
        """Rebuild and replace the system prompt.

        Useful after load_session_state to ensure the prompt reflects
        the latest AGENTS.md / SOUL.md / PROFILE.md on disk.

        Updates both self._sys_prompt and the first system-role
        message stored in self.memory.content (if one exists).
        """
        self._sys_prompt = self._build_sys_prompt()

        for msg, _marks in self.memory.content:
            if msg.role == "system":
                msg.content = self.sys_prompt
            break

    async def register_mcp_clients(self) -> None:
        """Register MCP clients on this agent's toolkit after construction."""
        for client in self._mcp_clients:
            await self.toolkit.register_mcp_client(client)

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """Override reply to process file blocks and handle commands.

        Args:
            msg: Input message(s) from user
            structured_model: Optional pydantic model for structured output

        Returns:
            Response message
        """
        # Process file and media blocks in messages
        if msg is not None:
            await process_file_and_media_blocks_in_message(msg)

        # Check if message is a system command
        last_msg = msg[-1] if isinstance(msg, list) else msg
        query = (
            last_msg.get_text_content() if isinstance(last_msg, Msg) else None
        )

        if self.command_handler.is_command(query):
            logger.info(f"Received command: {query}")
            msg = await self.command_handler.handle_command(query)
            await self.print(msg)
            return msg

        # Capability-aware routing: media input goes to prepass only when
        # primary LLM lacks required modalities.
        capabilities = self._message_media_capabilities(msg)
        if capabilities and self._should_route_to_vlm(msg, capabilities):
            raw_analyses: list[str] = []
            readable_analyses: list[str] = []
            failures: list[str] = []
            source = self._get_last_message(msg)
            user_text = source.get_text_content() if isinstance(source, Msg) else ""
            for capability in _MEDIA_CAPABILITIES_ORDER:
                if capability not in capabilities:
                    continue
                result = await self._run_media_understanding(msg, capability)
                if result.decision.outcome == "success" and result.analysis:
                    logger.info(
                        "%s prepass completed with %s/%s (%d item(s), %d attempt(s))",
                        capability,
                        result.used.provider_id if result.used else "unknown",
                        result.used.model if result.used else "unknown",
                        result.decision.selected_item_count,
                        len(result.decision.attempts),
                    )
                    raw_analyses.append(f"[{capability}] {result.analysis}")
                    readable = format_vlm_prepass_context(
                        capability,
                        result.analysis,
                        user_text=user_text,
                    )
                    if readable:
                        readable_analyses.append(readable)
                else:
                    reason = result.decision.reason or result.decision.outcome
                    logger.warning(
                        "%s prepass unavailable (%s); continue with degraded context",
                        capability,
                        reason,
                    )
                    failures.append(f"{capability}: {reason}")

            if raw_analyses:
                msg = self._inject_vlm_analysis_for_llm(
                    msg,
                    raw_analysis="\n".join(raw_analyses),
                    readable_analysis="\n\n".join(readable_analyses),
                )
            if failures:
                msg = self._inject_vlm_failure_for_llm(
                    msg,
                    "; ".join(failures),
                )

        # Normal message processing (or no VLM configured)
        return await super().reply(msg=msg, structured_model=structured_model)

    def _should_route_to_vlm(
        self,
        msg: Msg | list[Msg] | None,
        capabilities: set[str] | None = None,
    ) -> bool:
        caps = capabilities or self._message_media_capabilities(msg)
        if not caps:
            logger.debug("Media routing skipped: no media blocks")
            return False
        if self._active_llm_cfg is not None and all(
            supports_input_capability(self._active_llm_cfg, cap) for cap in caps
        ):
            logger.debug(
                "Media routing skipped: active LLM supports requested capabilities (%s/%s)",
                self._active_llm_cfg.provider_id,
                self._active_llm_cfg.model,
            )
            return False
        if self._vlm_model is not None:
            logger.debug("Vision routing enabled: using active VLM model")
            return True
        use_fallback = len(self._vlm_fallback_models) > 0
        if use_fallback:
            logger.debug("Vision routing enabled: using VLM fallback chain only")
        else:
            logger.debug("Vision routing skipped: no VLM configured")
        return use_fallback

    @staticmethod
    def _message_media_capabilities(msg: Msg | list[Msg] | None) -> set[str]:
        messages = (
            [msg] if isinstance(msg, Msg) else msg if isinstance(msg, list) else []
        )
        capabilities: set[str] = set()
        for message in messages:
            if not isinstance(message, Msg):
                continue
            if not isinstance(message.content, list):
                continue
            for block in message.content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in {"image", "audio", "video"}:
                    capabilities.add(block_type)
        return capabilities

    async def _run_media_understanding(
        self,
        msg: Msg | list[Msg] | None,
        capability: str,
    ):
        settings = getattr(self._vision_settings, capability)
        attachments_mode, max_items = self._resolve_media_selection_policy(
            capability=capability,
            settings=settings,
        )

        return await run_media_understanding_prepass(
            msg=msg,
            capability=capability,
            enabled=settings.enabled,
            attachments_mode=attachments_mode,
            max_items=max_items,
            prompt_override=settings.prompt_override,
            timeout_seconds=settings.timeout_seconds,
            max_output_chars=settings.max_output_chars,
            active_vlm_cfg=self._active_vlm_cfg,
            vlm_fallback_models=self._vlm_fallback_models,
            active_vlm_model=self._vlm_model,
            run_with_runtime_model=self._run_runtime_prepass,
        )

    @staticmethod
    def _resolve_media_selection_policy(
        *,
        capability: str,
        settings: Any,
    ) -> tuple[str, int]:
        env_mode = os.getenv(
            f"COPAW_{capability.upper()}_ATTACHMENTS_MODE",
            "",
        ).strip().lower()
        # Keep old image env compatibility.
        if capability == "image" and not env_mode:
            env_mode = os.getenv("COPAW_VISION_ATTACHMENTS_MODE", "").strip().lower()
        attachments_mode = env_mode if env_mode in {"first", "all"} else settings.attachments_mode

        env_max_raw = os.getenv(f"COPAW_{capability.upper()}_MAX_ITEMS", "").strip()
        # Keep old image env compatibility.
        if capability == "image" and not env_max_raw:
            env_max_raw = os.getenv("COPAW_VISION_MAX_IMAGES", "").strip()

        default_max = getattr(settings, "max_images", None) or settings.max_items
        try:
            max_items = max(1, int(env_max_raw)) if env_max_raw else default_max
        except ValueError:
            max_items = default_max
        return attachments_mode, max_items

    async def _run_runtime_prepass(
        self,
        runtime_model,
        msg: Msg,
        timeout_seconds: int,
    ) -> str:
        prepass_reply = await asyncio.wait_for(
            self._reply_with_runtime_model(
                runtime_model,
                msg=msg,
                structured_model=None,
                persist_to_memory=False,
            ),
            timeout=max(1, timeout_seconds),
        )
        analysis = prepass_reply.get_text_content()
        if not analysis:
            raise RuntimeError("VLM prepass returned empty analysis")
        return analysis

    @staticmethod
    def _get_last_message(msg: Msg | list[Msg] | None) -> Msg:
        return get_last_message(msg)

    def _inject_vlm_analysis_for_llm(
        self,
        msg: Msg | list[Msg] | None,
        raw_analysis: str,
        readable_analysis: str = "",
    ) -> Msg | list[Msg] | None:
        """Inject both raw and readable media analysis into LLM context."""
        target = self._get_last_message(msg)
        sections = [
            "[VisionPrepass]",
            raw_analysis,
            "[/VisionPrepass]",
        ]
        if readable_analysis.strip():
            sections.extend(
                [
                    "",
                    "[MediaUnderstanding]",
                    readable_analysis,
                    "[/MediaUnderstanding]",
                ],
            )
        analysis_block = TextBlock(
            type="text",
            text="\n".join(sections),
        )

        if isinstance(target.content, list):
            target.content = [*target.content, analysis_block]
        elif isinstance(target.content, str):
            target.content = (
                f"{target.content}\n\n[Vision analysis from helper model]\n"
                f"{analysis_block.text}\n[End vision analysis]"
            )
        else:
            target.content = [analysis_block]
        return msg

    def _inject_vlm_failure_for_llm(
        self,
        msg: Msg | list[Msg] | None,
        error_text: str,
    ) -> Msg | list[Msg] | None:
        """Inject graceful degradation note when VLM prepass fails."""
        target = self._get_last_message(msg)
        if isinstance(target.content, list):
            target.content = [
                *target.content,
                TextBlock(
                    type="text",
                    text=(
                        "[VisionPrepassFailed]\n"
                        "Media analysis is unavailable for this turn. "
                        "Proceed with best-effort text-only reasoning and "
                        "state visual uncertainty explicitly.\n"
                        f"Reason: {error_text}\n"
                        "[/VisionPrepassFailed]"
                    ),
                ),
            ]
        return msg

    async def _reply_with_runtime_model(
        self,
        runtime_model,
        msg: Msg | list[Msg] | None,
        structured_model: Type[BaseModel] | None,
        persist_to_memory: bool = True,
    ) -> Msg:
        original_model = self.model
        old_memory_chat_model = None
        original_memory_len = len(self.memory.content)
        if self.memory_manager is not None:
            old_memory_chat_model = self.memory_manager.chat_model

        self.model = runtime_model
        if self.memory_manager is not None:
            self.memory_manager.chat_model = runtime_model
        try:
            return await super().reply(msg=msg, structured_model=structured_model)
        finally:
            if not persist_to_memory and len(self.memory.content) > original_memory_len:
                self.memory.content = self.memory.content[:original_memory_len]
            self.model = original_model
            if self.memory_manager is not None:
                self.memory_manager.chat_model = old_memory_chat_model

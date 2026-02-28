# -*- coding: utf-8 -*-
"""CoPaw Agent - Main agent implementation.

This module provides the main CoPawAgent class built on ReActAgent,
with integrated tools, skills, and memory management.
"""
import logging
import os
from typing import Any, List, Optional, Type

from agentscope.agent import ReActAgent
from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit
from pydantic import BaseModel

from .command_handler import CommandHandler
from .hooks import BootstrapHook, MemoryCompactionHook
from .memory import CoPawInMemoryMemory
from .model_capabilities import supports_vision
from .model_factory import (
    create_model_and_formatter,
    create_model_from_config,
    create_text_and_vlm_models,
)
from .model_fallback import run_with_vlm_fallback
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
)

logger = logging.getLogger(__name__)


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

        # Capability-aware routing: image input goes to VLM only when primary
        # LLM does not support vision. VLM output is treated as auxiliary
        # context and then handed back to LLM for final task completion.
        if self._should_route_to_vlm(msg):
            try:
                analysis = await self._run_vlm_prepass(msg)
                msg = self._inject_vlm_analysis_for_llm(msg, analysis)
            except Exception as e:
                logger.warning("VLM prepass failed; continue with degraded context: %s", e)
                msg = self._inject_vlm_failure_for_llm(msg, str(e))

        # Normal message processing (or no VLM configured)
        return await super().reply(msg=msg, structured_model=structured_model)

    def _should_route_to_vlm(self, msg: Msg | list[Msg] | None) -> bool:
        if not self._message_has_image_blocks(msg):
            return False
        if self._active_llm_cfg is not None and supports_vision(
            self._active_llm_cfg,
        ):
            return False
        if self._vlm_model is not None:
            return True
        return len(self._vlm_fallback_models) > 0

    @staticmethod
    def _message_has_image_blocks(msg: Msg | list[Msg] | None) -> bool:
        messages = (
            [msg] if isinstance(msg, Msg) else msg if isinstance(msg, list) else []
        )
        for message in messages:
            if not isinstance(message, Msg):
                continue
            if not isinstance(message.content, list):
                continue
            for block in message.content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
        return False

    async def _run_vlm_prepass(self, msg: Msg | list[Msg] | None) -> str:
        """Run a vision-only prepass and return text analysis."""
        if self._active_vlm_cfg is None and len(self._vlm_fallback_models) == 0:
            raise RuntimeError("No VLM configured for image analysis")

        vlm_msg = self._build_vlm_prepass_message(msg)

        model_map = {}
        if self._active_vlm_cfg is not None and self._vlm_model is not None:
            key = (self._active_vlm_cfg.provider_id, self._active_vlm_cfg.model)
            model_map[key] = self._vlm_model
        for cfg, model in self._vlm_fallback_models:
            key = (cfg.provider_id, cfg.model)
            model_map[key] = model

        fallbacks = [cfg for cfg, _ in self._vlm_fallback_models]
        if self._active_vlm_cfg is None:
            primary = fallbacks[0]
            fallbacks = fallbacks[1:]
        else:
            primary = self._active_vlm_cfg

        async def _run(cfg):
            key = (cfg.provider_id, cfg.model)
            runtime_model = model_map[key]
            prepass_reply = await self._reply_with_runtime_model(
                runtime_model,
                msg=vlm_msg,
                structured_model=None,
                persist_to_memory=False,
            )
            analysis = prepass_reply.get_text_content()
            if not analysis:
                raise RuntimeError("VLM prepass returned empty analysis")
            return analysis

        result = await run_with_vlm_fallback(primary, fallbacks, _run)
        logger.info(
            "VLM prepass completed with %s/%s",
            result.used.provider_id,
            result.used.model,
        )
        return result.result

    def _build_vlm_prepass_message(self, msg: Msg | list[Msg] | None) -> Msg:
        source = self._get_last_message(msg)
        blocks = source.content if isinstance(source.content, list) else []
        image_blocks = [
            block
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        user_text = source.get_text_content() or ""
        prompt = (
            "You are a vision preprocessor for a stronger text-only planner.\n"
            "Analyze the provided images and return structured notes only.\n"
            "Do NOT answer the user directly and do NOT invent unseen details.\n\n"
            "Required output sections:\n"
            "1) OCR text\n"
            "2) Key objects/entities\n"
            "3) Spatial/layout cues relevant to task\n"
            "4) Confidence and ambiguities\n"
            "5) Suggested follow-up checks for the planner\n\n"
            f"User task:\n{user_text}"
        )
        content = [TextBlock(type="text", text=prompt), *image_blocks]
        return Msg(name=source.name, role="user", content=content)

    @staticmethod
    def _get_last_message(msg: Msg | list[Msg] | None) -> Msg:
        if isinstance(msg, list):
            for item in reversed(msg):
                if isinstance(item, Msg):
                    return item
        if isinstance(msg, Msg):
            return msg
        return Msg(
            name="user",
            role="user",
            content=[TextBlock(type="text", text="")],
        )

    def _inject_vlm_analysis_for_llm(
        self,
        msg: Msg | list[Msg] | None,
        analysis: str,
    ) -> Msg | list[Msg] | None:
        """Remove raw image blocks and inject VLM analysis back to LLM context."""
        target = self._get_last_message(msg)
        analysis_block = TextBlock(
            type="text",
            text=(
                "[VisionPrepass]\n"
                f"{analysis}\n"
                "[/VisionPrepass]"
            ),
        )

        if isinstance(target.content, list):
            filtered = [
                block
                for block in target.content
                if not (
                    isinstance(block, dict) and block.get("type") == "image"
                )
            ]
            filtered.append(analysis_block)
            target.content = filtered
        elif isinstance(target.content, str):
            target.content = (
                f"{target.content}\n\n[Vision analysis from helper model]\n"
                f"{analysis}\n[End vision analysis]"
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
            filtered = [
                block
                for block in target.content
                if not (
                    isinstance(block, dict) and block.get("type") == "image"
                )
            ]
            filtered.append(
                TextBlock(
                    type="text",
                    text=(
                        "[VisionPrepassFailed]\n"
                        "Image analysis is unavailable for this turn. "
                        "Proceed with best-effort text-only reasoning and "
                        "state visual uncertainty explicitly.\n"
                        f"Reason: {error_text}\n"
                        "[/VisionPrepassFailed]"
                    ),
                ),
            )
            target.content = filtered
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

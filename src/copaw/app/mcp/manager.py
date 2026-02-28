# -*- coding: utf-8 -*-
"""MCP client manager for hot-reloadable client lifecycle management.

This module provides centralized management of MCP clients with support
for runtime updates without restarting the application.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

from agentscope.mcp import StdIOStatefulClient

if TYPE_CHECKING:
    from ...config.config import MCPClientConfig, MCPConfig

logger = logging.getLogger(__name__)

_ENV_REF_PATTERN = re.compile(r"^\$\{([^}]+)\}$")


def _build_mcp_env(config_env: Dict[str, str] | None) -> Dict[str, str]:
    """Build env for MCP subprocess: inherit os.environ, apply config env.

    - Resolves ${VAR} in config env from os.environ (so you can set keys only
      in Settings → Environments and use \"TAVILY_API_KEY\": \"${TAVILY_API_KEY}\"
      in MCP).
    - Only non-empty resolved values override; empty does not overwrite
      os.environ (so key from Environments is kept if MCP env has empty value).
    """
    out = dict(os.environ)
    if not config_env:
        return out
    for k, v in config_env.items():
        if not isinstance(v, str):
            continue
        v = v.strip()
        m = _ENV_REF_PATTERN.match(v)
        if m:
            v = os.environ.get(m.group(1), "")
        if v:
            out[k] = v
    return out


def _adapt_command_for_windows(command: str, args: List[str]) -> Tuple[str, List[str]]:
    """On Windows, run 'npx' via cmd /c so PATH is resolved (e.g. npx.cmd)."""
    if platform.system() != "Windows":
        return command, list(args)
    cmd = command.strip().lower()
    if cmd == "npx" or cmd.endswith(os.sep + "npx") or cmd.endswith("/npx"):
        return "cmd", ["/c", "npx"] + list(args)
    return command, list(args)


class MCPClientManager:
    """Manages MCP clients with hot-reload support.

    This manager handles the lifecycle of MCP clients, including:
    - Initial loading from config
    - Runtime replacement when config changes
    - Cleanup on shutdown

    Design pattern mirrors ChannelManager for consistency.
    """

    def __init__(self) -> None:
        """Initialize an empty MCP client manager."""
        self._clients: Dict[str, StdIOStatefulClient] = {}
        self._lock = asyncio.Lock()

    async def init_from_config(self, config: "MCPConfig") -> None:
        """Initialize clients from configuration.

        Args:
            config: MCP configuration containing client definitions
        """
        logger.debug("Initializing MCP clients from config")
        for key, client_config in config.clients.items():
            if not client_config.enabled:
                logger.debug(f"MCP client '{key}' is disabled, skipping")
                continue

            try:
                await self._add_client(key, client_config)
                logger.debug(f"MCP client '{key}' initialized successfully")
            except Exception as e:
                logger.warning(
                    "Failed to initialize MCP client '%s': %s. "
                    "Check that the command is installed (e.g. npx for Node-based servers) and any required env/API keys are set.",
                    key,
                    e,
                )
                logger.debug("MCP client init failure", exc_info=True)

    async def get_clients(self) -> List[Any]:
        """Get list of all active MCP clients.

        This method is called by the runner on each query to get
        the latest set of clients.

        Returns:
            List of connected MCP client instances
        """
        async with self._lock:
            return [
                client
                for client in self._clients.values()
                if client is not None
            ]

    async def replace_client(
        self,
        key: str,
        client_config: "MCPClientConfig",
        timeout: float = 60.0,
    ) -> None:
        """Replace or add a client with new configuration.

        Flow: connect new (outside lock) → swap + close old (inside lock).
        This ensures minimal lock holding time.

        Args:
            key: Client identifier (from config)
            client_config: New client configuration
            timeout: Connection timeout in seconds (default 60s)
        """
        # 1. Create and connect new client outside lock (may be slow)
        logger.debug(f"Connecting new MCP client: {key}")
        env = _build_mcp_env(client_config.env)
        cmd, args = _adapt_command_for_windows(
            client_config.command, client_config.args
        )
        new_client = StdIOStatefulClient(
            name=client_config.name,
            command=cmd,
            args=args,
            env=env,
        )

        try:
            # Add timeout to prevent indefinite blocking
            await asyncio.wait_for(new_client.connect(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout connecting MCP client '{key}' after {timeout}s",
            )
            try:
                await new_client.close()
            except Exception:
                pass
            raise
        except Exception as e:
            logger.warning(f"Failed to connect MCP client '{key}': {e}")
            try:
                await new_client.close()
            except Exception:
                pass
            raise

        # 2. Swap and close old client inside lock
        async with self._lock:
            old_client = self._clients.get(key)
            self._clients[key] = new_client

            if old_client is not None:
                logger.debug(f"Closing old MCP client: {key}")
                try:
                    await old_client.close()
                except Exception as e:
                    logger.warning(
                        f"Error closing old MCP client '{key}': {e}",
                    )
            else:
                logger.debug(f"Added new MCP client: {key}")

    async def remove_client(self, key: str) -> None:
        """Remove and close a client.

        Args:
            key: Client identifier to remove
        """
        async with self._lock:
            old_client = self._clients.pop(key, None)

        if old_client is not None:
            logger.debug(f"Removing MCP client: {key}")
            try:
                await old_client.close()
            except Exception as e:
                logger.warning(f"Error closing MCP client '{key}': {e}")

    async def close_all(self) -> None:
        """Close all MCP clients.

        Called during application shutdown.
        """
        async with self._lock:
            clients_snapshot = list(self._clients.items())
            self._clients.clear()

        logger.debug("Closing all MCP clients")
        for key, client in clients_snapshot:
            if client is not None:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing MCP client '{key}': {e}")

    async def _add_client(
        self,
        key: str,
        client_config: "MCPClientConfig",
        timeout: float = 60.0,
    ) -> None:
        """Add a new client (used during initial setup).

        Args:
            key: Client identifier
            client_config: Client configuration
            timeout: Connection timeout in seconds (default 60s)
        """
        env = _build_mcp_env(client_config.env)
        cmd, args = _adapt_command_for_windows(
            client_config.command, client_config.args
        )
        client = StdIOStatefulClient(
            name=client_config.name,
            command=cmd,
            args=args,
            env=env,
        )

        # Add timeout to prevent indefinite blocking
        await asyncio.wait_for(client.connect(), timeout=timeout)

        async with self._lock:
            self._clients[key] = client

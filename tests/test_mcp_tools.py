# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP tools act as the task's user, and a tool the server says writes is always confirmed.

Runnable standalone (``python tests/test_mcp_tools.py``) or via pytest.
No network: the MCP client is replaced by one that records the connections it is
given and lists tools converted the way langchain-mcp-adapters converts them.
"""
import asyncio
import base64
import json
import os
import sys
import tempfile

os.environ.setdefault("APP_ID", "context_agent")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "9081")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("APP_VERSION", "2.10.0")
os.environ.setdefault("NEXTCLOUD_URL", "http://127.0.0.1:8080")
os.environ.setdefault("APP_PERSISTENT_STORAGE", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool  # noqa: E402
from mcp.types import Tool, ToolAnnotations  # noqa: E402
from nc_py_api import AsyncNextcloudApp  # noqa: E402

from ex_app.lib.all_tools import mcp  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import MCP_ALWAYS_ALLOW_SETTING_ID, is_always_allowed  # noqa: E402

ON_NEXTCLOUD = {
	"url": os.environ["NEXTCLOUD_URL"] + "/index.php/apps/app_api/proxy/mcp_connector/mcp",
	"transport": "streamable_http",
}
ELSEWHERE = {"url": "https://mcp.example.com/mcp", "transport": "streamable_http"}


def _server_tool(name, annotations=None):
	return Tool(name=name, inputSchema={"type": "object", "properties": {}}, annotations=annotations)


SERVER_TOOLS = [
	_server_tool("read_note", ToolAnnotations(readOnlyHint=True)),
	_server_tool("create_note", ToolAnnotations(readOnlyHint=False, destructiveHint=False)),
	# readOnlyHint defaults to false: a server that annotates a tool without it says it writes
	_server_tool("update_note", ToolAnnotations(destructiveHint=True)),
	# not annotated at all: the server does not classify it
	_server_tool("get_forecast"),
]


class FakeMCPClient:
	"""Records the connections it is given, lists SERVER_TOOLS."""

	connections = None

	def __init__(self, connections):
		FakeMCPClient.connections = connections

	async def get_tools(self):
		return [convert_mcp_tool_to_langchain_tool(None, t, connection=ON_NEXTCLOUD) for t in SERVER_TOOLS]


class FakeAppConfig:
	def __init__(self, values):
		self.values = values

	async def get_value(self, key, default=None):
		return self.values.get(key, default)


def _load_tools(user, mcp_config, always_allow=""):
	async def run():
		nc = AsyncNextcloudApp(user=user)
		nc.appconfig_ex = FakeAppConfig({
			"mcp_config": json.dumps(mcp_config),
			MCP_ALWAYS_ALLOW_SETTING_ID: always_allow,
		})
		return await mcp.get_tools(nc)

	real_client, mcp.MultiServerMCPClient = mcp.MultiServerMCPClient, FakeMCPClient
	try:
		return asyncio.run(run()), FakeMCPClient.connections
	finally:
		mcp.MultiServerMCPClient = real_client


def test_calls_to_this_nextcloud_are_signed_for_the_tasks_user():
	for user in ("alice", "bob"):
		_, connections = _load_tools(user, {"nextcloud": ON_NEXTCLOUD, "elsewhere": ELSEWHERE})
		headers = connections["nextcloud"]["headers"]
		assert base64.b64decode(headers["AUTHORIZATION-APP-API"]).decode() == f"{user}:{os.environ['APP_SECRET']}"
		assert headers["EX-APP-ID"] == os.environ["APP_ID"]
		assert headers["EX-APP-VERSION"] == os.environ["APP_VERSION"]
		assert headers["AA-VERSION"]
		# the app secret never leaves this Nextcloud
		assert connections["elsewhere"] == ELSEWHERE


def test_a_tool_the_server_says_writes_is_never_always_allowed():
	tools, _ = _load_tools("alice", {"nextcloud": ON_NEXTCLOUD}, always_allow="*")
	assert {t.name for t in tools if is_always_allowed(t)} == {"mcp_read_note", "mcp_get_forecast"}

	tools, _ = _load_tools("alice", {"nextcloud": ON_NEXTCLOUD}, always_allow="read_note, mcp_create_note, update_note")
	assert {t.name for t in tools if is_always_allowed(t)} == {"mcp_read_note"}


if __name__ == "__main__":
	for name, fn in list(globals().items()):
		if name.startswith("test_") and callable(fn):
			fn()
			print(f"ok {name}")

# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from base64 import b64encode
from json import JSONDecodeError

from langchain_mcp_adapters.client import MultiServerMCPClient
from nc_py_api import AsyncNextcloudApp
import json
import re
from ex_app.lib.all_tools.lib.impulse import MCP_ALWAYS_ALLOW_SETTING_ID, always_allow
from ex_app.lib.logger import log
from nc_py_api.ex_app import LogLvl
import asyncio
import traceback


async def get_tools(nc: AsyncNextcloudApp):
	mcp_json = await nc.appconfig_ex.get_value("mcp_config", "{}")
	try:
		mcp_config = json.loads(mcp_json)
	except JSONDecodeError:
		await log(nc, LogLvl.ERROR, "Invalid MCP json config: " + mcp_json)
		mcp_config = {}
	always_allowed = parse_always_allow(await nc.appconfig_ex.get_value(MCP_ALWAYS_ALLOW_SETTING_ID, default=""))
	try:
		server = MultiServerMCPClient(await sign_nextcloud_servers(nc, mcp_config))
		tools = await asyncio.wait_for(server.get_tools(), timeout=120)
		for tool in tools:
			# MCP tools cannot be classified, so they are confirmed as the worst case
			# unless the admin vouched for them; a tool the server says writes never is
			if not is_write(tool) and ("*" in always_allowed or tool.name in always_allowed):
				always_allow(tool.coroutine)
			tool.name = "mcp_" + tool.name
		return tools
	except Exception as e:
		tb_str = "".join(traceback.format_exception(e))
		await log(nc, LogLvl.ERROR, "Failed to load MCP servers: " + tb_str)
	return []


async def sign_nextcloud_servers(nc: AsyncNextcloudApp, mcp_config: dict) -> dict:
	"""Sign the requests to MCP servers on this Nextcloud for the user of the task.

	They carry the same AppAPI headers as this app's own requests to Nextcloud, so a
	server behind the AppAPI proxy runs each call as that user. Servers elsewhere get
	no headers: the app secret never leaves this Nextcloud.
	"""
	cfg = nc.app_cfg
	headers = {
		"AA-VERSION": cfg.aa_version,
		"EX-APP-ID": cfg.app_name,
		"EX-APP-VERSION": cfg.app_version,
		"AUTHORIZATION-APP-API": b64encode(f"{await nc.user}:{cfg.app_secret}".encode()).decode(),
	}
	signed = {}
	for name, connection in mcp_config.items():
		if str(connection.get("url", "")).startswith(cfg.endpoint + "/"):
			connection = {**connection, "headers": {**(connection.get("headers") or {}), **headers}}
		signed[name] = connection
	return signed


def is_write(tool) -> bool:
	"""Whether the MCP server says this tool can change something.

	langchain-mcp-adapters puts every field of the tool's MCP annotations in its
	metadata. readOnlyHint defaults to false, so an annotated tool writes unless it is
	marked read-only; a tool without annotations is not classified by its server.
	"""
	annotations = tool.metadata or {}
	return "readOnlyHint" in annotations and annotations["readOnlyHint"] is not True


def parse_always_allow(value) -> set[str]:
	"""Turn the admin's list of always allowed MCP tools into a set of names.

	Names are taken with or without the "mcp_" prefix the agent shows them with.
	"""
	names = set()
	for name in re.split(r"[,\s]+", str(value or "")):
		if name.startswith("mcp_"):
			name = name[len("mcp_"):]
		if name:
			names.add(name)
	return names


def get_category_name():
	return "MCP Server"


async def is_available(nc: AsyncNextcloudApp):
	return True

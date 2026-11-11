"""main() picks the transport from MCP_TRANSPORT, defaulting to stdio."""

import pytest

from servicetitan_mcp import server


@pytest.mark.parametrize(
    "env, expected",
    [
        (None, "stdio"),
        ("", "stdio"),
        ("  ", "stdio"),
        ("sse", "sse"),
        (" SSE ", "sse"),
        ("streamable-http", "streamable-http"),
    ],
)
def test_main_transport_from_env(monkeypatch, env, expected):
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda transport: calls.append(transport))
    if env is None:
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    else:
        monkeypatch.setenv("MCP_TRANSPORT", env)
    server.main()
    assert calls == [expected]

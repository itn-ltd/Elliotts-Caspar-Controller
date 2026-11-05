"""AMCP client against the fake server."""

import pytest
from conftest import free_port
from fake_amcp import FakeAMCPServer

from caspar_controller.caspar.amcp import AMCPClient


@pytest.fixture
def server():
    with FakeAMCPServer() as s:
        yield s


def test_send_returns_stripped_reply(server):
    reply = AMCPClient(port=server.port).send("PLAY 1-1 AMB")
    assert reply == "202 PLAY OK"
    assert server.commands == ["PLAY 1-1 AMB"]


def test_multi_line_reply_is_returned_whole(server):
    assert AMCPClient(port=server.port).send("VERSION") == "201 VERSION OK\r\n2.3.3 Stable"


def test_ping_uses_version(server):
    assert AMCPClient(port=server.port).ping() is True
    assert server.commands == ["VERSION"]


def test_ping_false_on_error_reply():
    with FakeAMCPServer(replies={"VERSION": "500 FAILED"}) as s:
        assert AMCPClient(port=s.port).ping() is False


def test_unreachable_server_returns_error_string():
    client = AMCPClient(port=free_port(), timeout=0.5)
    assert client.send("VERSION").startswith("ERROR: ")
    assert client.ping() is False


def test_command_helpers(server):
    client = AMCPClient(port=server.port)
    client.play_html(1, "https://example.com/gfx?a=1")
    client.info_layer()
    assert server.commands == ['PLAY 1-1 [HTML] "https://example.com/gfx?a=1"', "INFO 1-1"]

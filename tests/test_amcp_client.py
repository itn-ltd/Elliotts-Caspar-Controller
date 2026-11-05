"""AMCP client against the fake server."""

import time

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


def test_200_reply_reads_data_lines_to_the_blank_line():
    with FakeAMCPServer(replies={"CLS": "200 CLS OK\r\nAMB\r\nCLIP 2\r\n"}) as s:
        assert AMCPClient(port=s.port).send("CLS") == "200 CLS OK\r\nAMB\r\nCLIP 2"


def test_201_xml_over_several_lines_is_read_whole():
    xml = '<?xml version="1.0"?>\r\n<layer>\r\n   <foreground/>\r\n</layer>'
    with FakeAMCPServer(info_xml=xml) as s:
        client = AMCPClient(port=s.port)
        assert client.info_layer() == "201 INFO OK\r\n" + xml
        assert client.ping() is True  # the next reply isn't confused with leftover XML


def test_error_reply_is_one_line():
    with FakeAMCPServer(replies={"PLAY": "404 PLAY FAILED"}) as s:
        assert AMCPClient(port=s.port).send("PLAY 1-1 NOPE") == "404 PLAY FAILED"


def test_one_connection_is_kept_open(server):
    client = AMCPClient(port=server.port)
    client.send("VERSION")
    client.send("INFO 1-1")
    assert len(server.connections) == 1


def test_reconnects_after_caspar_drops_the_connection(server):
    client = AMCPClient(port=server.port)
    client.send("VERSION")
    server.drop_connections()
    assert client.send("PLAY 1-1 AMB") == "202 PLAY OK"
    assert server.commands == ["VERSION", "PLAY 1-1 AMB"]  # sent once, not repeated
    assert len(server.connections) == 2


def test_bye_does_not_wait_for_a_reply(server):
    client = AMCPClient(port=server.port)
    t0 = time.perf_counter()
    assert client.send("BYE") == ""
    assert time.perf_counter() - t0 < 0.3


def test_local_call_is_fast(server):
    client = AMCPClient(port=server.port)
    client.ping()  # connect
    t0 = time.perf_counter()
    for _ in range(10):
        assert client.ping()
    assert (time.perf_counter() - t0) / 10 < 0.05


def test_leftover_lines_dont_shift_later_replies():
    # CasparCG echoes a bad command after 400, and pretty XML can leave a blank line behind.
    xml = "<layer/>\r\n"
    with FakeAMCPServer(info_xml=xml, replies={"PLAY": "400 ERROR\r\nPLAY 1-1 TYPO"}) as s:
        client = AMCPClient(port=s.port)
        assert client.send("PLAY 1-1 TYPO") == "400 ERROR"
        assert client.info_layer() == "201 INFO OK\r\n<layer/>"
        assert client.send("VERSION") == "201 VERSION OK\r\n2.3.3 Stable"

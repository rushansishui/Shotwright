"""启动服务时端口被占用或被系统保留：自动换下一个可用端口。"""

import socket

from shotwright.cli import bind_port


def test_bind_port_uses_the_preferred_port_when_free():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sock = bind_port("127.0.0.1", port)
    try:
        assert sock.getsockname()[1] == port
    finally:
        sock.close()


def test_bind_port_skips_a_port_that_is_taken():
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    port = taken.getsockname()[1]
    try:
        sock = bind_port("127.0.0.1", port)
        try:
            assert sock.getsockname()[1] != port
        finally:
            sock.close()
    finally:
        taken.close()


def test_bind_port_falls_back_to_any_free_port(monkeypatch):
    real_bind = socket.socket.bind

    def refuse_fixed(self, address):
        if address[1] != 0:
            raise PermissionError(10013, "以一种访问权限不允许的方式做了一个访问套接字的尝试。")
        return real_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", refuse_fixed)
    sock = bind_port("127.0.0.1", 8000, tries=3)
    try:
        assert sock.getsockname()[1] != 0
    finally:
        sock.close()

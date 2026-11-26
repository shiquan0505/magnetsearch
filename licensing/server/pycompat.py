#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小兼容层：让授权服务在 Python 3.6（阿里云 Alinux 3 自带）上也能跑。

ThreadingHTTPServer 是 3.7 才有的，这里用 ThreadingMixIn 组一个等价物。
其余用到的标准库接口（sqlite3 UPSERT、secrets、json）在 3.6 都已具备。
"""

try:                                     # Python 3.7+
    from http.server import ThreadingHTTPServer  # noqa: F401
except ImportError:                      # Python 3.6
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

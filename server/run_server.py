#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyInstaller 入口（也可直接 python3 run_server.py 运行）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torznab_server

if __name__ == '__main__':
    torznab_server.main()

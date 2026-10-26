# -*- coding: utf-8 -*-
"""pytest 全局配置：把持久化指向临时文件，并隔离房间数据。"""
import os

TEST_DATA_FILE = "/tmp/werewolf_test_rooms.json"
os.environ["WEREWOLF_DATA_FILE"] = TEST_DATA_FILE

import pytest  # noqa: E402

import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_rooms():
    app.ROOMS.clear()
    yield
    app.ROOMS.clear()
    if os.path.exists(TEST_DATA_FILE):
        os.remove(TEST_DATA_FILE)
    tmp = TEST_DATA_FILE + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()
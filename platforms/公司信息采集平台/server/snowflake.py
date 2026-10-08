# -*- coding: utf-8 -*-
"""雪花算法 ID 生成器（与后台区分 worker_id，避免撞号）。

从项目根目录的 id生成.py 提取，去掉了底部的测试 print。
"""
import time
import threading


class Snowflake:
    def __init__(self, worker_id=16, data_center_id=1):
        self.worker_id = worker_id          # 和后台区分
        self.data_center_id = data_center_id

        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

        self.sequence_bits = 12
        self.worker_bits = 5
        self.dc_bits = 5

        self.max_sequence = (1 << self.sequence_bits) - 1
        self.worker_shift = self.sequence_bits
        self.dc_shift = self.sequence_bits + self.worker_bits
        self.timestamp_shift = self.sequence_bits + self.worker_bits + self.dc_bits
        self.epoch = 0

    def generate(self):
        with self._lock:
            timestamp = int(time.time() * 1000)
            if timestamp < self.last_timestamp:
                raise Exception("clock moved backwards")
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & self.max_sequence
                if self.sequence == 0:
                    # 同一毫秒序列号耗尽，忙等到下一毫秒
                    while timestamp <= self.last_timestamp:
                        timestamp = int(time.time() * 1000)
            else:
                self.sequence = 0
            self.last_timestamp = timestamp

            result = (
                ((timestamp - self.epoch) << self.timestamp_shift)
                | (self.data_center_id << self.dc_shift)
                | (self.worker_id << self.worker_shift)
                | self.sequence
            )
            return str(result)


_snowflake = Snowflake()


def new_id():
    """生成一个新的雪花 ID（字符串，与 MySQL 表 id 字段一致）。"""
    return _snowflake.generate()

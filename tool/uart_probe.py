#!/usr/bin/env python3
"""串口原始字节探测：看清板子到底回了什么，不做任何解析。

用法：
  python tool/uart_probe.py COM12

建议跑两次：
  1) 按复位后 10 秒内跑 -> 应该看到 Bootloader 的应答
  2) 等 15 秒后再跑   -> 如果 App 在跑，看看它会不会乱发字节
"""

import os
import struct
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt  # noqa: E402


def dump(label, data):
    if not data:
        print(f"  {label}: <无数据>")
        return
    print(f"  {label}: {len(data)} 字节  {data.hex(' ')}")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM12"
    ser = serial.Serial(port, 115200, timeout=0.5)
    time.sleep(0.2)

    print(f"端口 {port} @115200")
    print("--- 1. 先看有没有残留字节（不做任何发送）")
    dump("残留", ser.read(256))

    ser.reset_input_buffer()

    print("--- 2. 发 INFO，等 300ms")
    ser.write(qt.make_packet(qt.BL_CMD_INFO, 0))
    ser.flush()
    time.sleep(0.3)
    dump("INFO 回复", ser.read(256))

    ser.reset_input_buffer()

    print("--- 3. 发 CONFIRM，等 300ms（无 pending 时应回 0x15）")
    ser.write(qt.make_packet(qt.BL_CMD_CONFIRM, 0))
    ser.flush()
    time.sleep(0.3)
    dump("CONFIRM 回复", ser.read(256))

    print("--- 4. 什么都不发，静默 2 秒，看板子是否自己在发数据")
    ser.reset_input_buffer()
    time.sleep(2.0)
    dump("静默期", ser.read(256))

    print("--- 5. 再发一次 INFO")
    ser.write(qt.make_packet(qt.BL_CMD_INFO, 0))
    ser.flush()
    time.sleep(0.3)
    dump("INFO 回复", ser.read(256))

    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

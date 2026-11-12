#!/usr/bin/env python3
"""人工断电测试（半自动）：脚本负责计时、判定和记录，你只负责拔插电源。

设计前提：断电可能会让 USB 串口设备一起消失（ST-Link 与串口常挂在同一个 USB 上），
所以脚本不假设端口一直在，掉线就重连，不靠修改 timeout 来探测。

它做的事：
  1. 随机时刻提示你拔电，覆盖"扇区擦除 + 前几个数据包"这段窗口
  2. 用 ACK 超时或串口掉线自动测出真正断电的时刻（精确到毫秒）
  3. 插回电源后自动重连、读 INFO，判定状态有没有被破坏
  4. 逐次记录并给出统计

它做不到的：毫秒级的物理断电。人手反应约 200-500 ms，所以覆盖的是"扇区擦除"
（几百毫秒）窗口；元数据写入（64 字节、1-2 ms）由软件注入测试覆盖。

用法：
  python tool/power_cut_test.py --port COM12 --bin firmware/LEDB.bin --runs 20
"""

import argparse
import csv
import os
import random
import struct
import sys
import threading
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt  # noqa: E402

NONE = 0xFFFFFFFF
# 单次读取窗口尽量短，这样"断电"能被及时（0.2s 内）发现；
# START 的 ACK 要等擦除完成（1-4s），用显式 deadline 轮询，而不是靠长超时。
PORT_TIMEOUT = 0.2
START_DEADLINE = 5.0
DATA_DEADLINE = 0.6
SLOT_NAMES = {NONE: "NONE", 0: "A", 1: "B"}


def slot_name(value):
    return SLOT_NAMES.get(value, f"0x{value:08X}")


class SerialLink:
    """可重连的串口：设备掉线后能自己回来。"""

    def __init__(self, port, baudrate):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.open()

    def open(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=PORT_TIMEOUT)
        time.sleep(0.2)

    def close(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def reconnect(self, seconds=25.0):
        self.close()
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=PORT_TIMEOUT)
                time.sleep(0.2)
                return True
            except Exception:
                time.sleep(0.3)
        return False


def send_and_wait(link, cmd, data=b"", deadline=DATA_DEADLINE, seq=0):
    """发送一帧并等 ACK/NACK。

    用短读 + 显式 deadline 轮询：既不会把"擦除慢"误判成断电，
    又能在真正断电后 0.2 s 内发现。
    """
    ser = link.ser
    ser.write(qt.make_packet(cmd, seq, data))
    ser.flush()

    end = time.perf_counter() + deadline
    while time.perf_counter() < end:
        b = ser.read(1)
        if b == b"\x06":
            return
        if b == b"\x15":
            raise RuntimeError(f"NACK: cmd=0x{cmd:02X} seq={seq}")
    raise TimeoutError(f"ACK 超时: cmd=0x{cmd:02X} seq={seq}")


def read_info(link):
    link.ser.reset_input_buffer()
    link.ser.write(qt.make_packet(qt.BL_CMD_INFO, 0))
    link.ser.flush()
    cmd, _seq, data = qt.recv_frame(link.ser)
    if cmd != qt.BL_CMD_INFO_RSP or len(data) != qt.INFO_PAYLOAD_LEN:
        raise RuntimeError("INFO 应答异常")
    keys = ["active", "pending", "rollback", "boot_count",
            "a_state", "b_state", "a_size", "b_size"]
    return dict(zip(keys, struct.unpack("<8I", data)))


def try_info(link):
    try:
        return read_info(link)
    except Exception:
        return None


def wait_bootloader(link, seconds=10.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        info = try_info(link)
        if info is not None:
            return info
        time.sleep(0.1)
    return None


def ensure_clean(link):
    info = read_info(link)
    if info["pending"] != NONE:
        send_and_wait(link, qt.BL_CMD_CONFIRM, deadline=2.0)
        info = read_info(link)
    return info


def run_once(link, firmware, args, rnd):
    pre = ensure_clean(link)
    if pre["active"] == NONE:
        return {"round": rnd, "phase": "-", "cut_at": "-", "reaction_ms": "-",
                "result": "ABORT", "note": "active=NONE，需先正常升级一次"}

    baseline = pre["active"]
    pre_pending = pre["pending"]
    app_size = len(firmware)
    start_data = struct.pack("<II", app_size, qt.crc32(firmware))

    t0 = time.perf_counter()
    prompt_at = random.uniform(args.delay_min, args.delay_max)
    fired = {"t": None}

    def prompt():
        fired["t"] = time.perf_counter() - t0
        print(f"\n  *** 现在拔电（3.3V）！***  [t={fired['t']:.2f}s]\n", flush=True)

    timer = threading.Timer(prompt_at, prompt)
    timer.start()

    phase = "erase(START)"
    cut_at = None
    by_timeout = False
    try:
        send_and_wait(link, qt.BL_CMD_START, start_data, deadline=START_DEADLINE)
        phase = "data"
        seq = 0
        for offset in range(0, app_size, args.chunk_size):
            chunk = firmware[offset:offset + args.chunk_size]
            send_and_wait(link, qt.BL_CMD_DATA, chunk, deadline=DATA_DEADLINE, seq=seq)
            seq += 1
    except TimeoutError:
        by_timeout = True
        cut_at = time.perf_counter() - t0
    except (RuntimeError, serial.SerialException, OSError):
        cut_at = time.perf_counter() - t0
    finally:
        timer.cancel()

    if cut_at is None:
        return {"round": rnd, "phase": phase, "cut_at": "-", "reaction_ms": "-",
                "result": "SKIP", "note": "没断电（整包传完了）"}

    reaction = int(max(0.0, cut_at - fired["t"]) * 1000) if fired["t"] is not None else None

    # 擦除阶段的断电只能靠"START 一直没 ACK"推断，因此时刻是上限而不是精确值
    cut_str = f"<={cut_at:.1f}" if (by_timeout and phase.startswith("erase")) else f"{cut_at:.2f}"
    if by_timeout and phase.startswith("erase"):
        reaction = None      # 真实拔电时刻未知，反应时间也就无从计算

    # 确认真断电：端口还在且设备立刻能应答，说明刚才只是断电太短或根本没断
    time.sleep(0.1)
    still_alive = try_info(link)
    if still_alive is not None:
        return {"round": rnd, "phase": phase, "cut_at": cut_str,
                "reaction_ms": reaction if reaction is not None else "-",
                "result": "SKIP",
                "note": "设备仍在应答：断电太短或没拔到位（拔掉后请保持 1 秒以上）"}

    input("  插回电源后按回车继续…")

    if not link.reconnect(25):
        return {"round": rnd, "phase": phase, "cut_at": cut_str,
                "reaction_ms": reaction if reaction is not None else "-",
                "result": "DEAD", "note": f"串口 {link.port} 没回来（换口了？）"}

    info = wait_bootloader(link, 10)
    if info is None:
        print("  没赶上启动窗口？请按一下复位键，脚本再等 10 秒…")
        info = wait_bootloader(link, 10)

    if info is None:
        return {"round": rnd, "phase": phase, "cut_at": cut_str,
                "reaction_ms": reaction if reaction is not None else "-",
                "result": "DEAD", "note": "上电后无响应，需人工救砖"}

    if info["active"] != baseline:
        return {"round": rnd, "phase": phase, "cut_at": cut_str,
                "reaction_ms": reaction if reaction is not None else "-",
                "result": "FAIL", "note": f"active 被改写: {slot_name(info['active'])}"}

    if info["pending"] != pre_pending:
        return {"round": rnd, "phase": phase, "cut_at": cut_str,
                "reaction_ms": reaction if reaction is not None else "-",
                "result": "FAIL", "note": f"pending 变化: {slot_name(info['pending'])}"}

    return {"round": rnd, "phase": phase, "cut_at": cut_str,
            "reaction_ms": reaction if reaction is not None else "-",
            "result": "OK", "note": "状态未破坏，旧固件仍可启动"}


def main():
    parser = argparse.ArgumentParser(description="人工断电测试（半自动）")
    parser.add_argument("--port", default="COM12")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--bin", required=True, help="用来升级的固件（要与目标槽地址匹配）")
    parser.add_argument("--runs", type=int, default=20, help="断电次数，默认 20")
    parser.add_argument("--delay-min", type=float, default=0.2, help="最早提示拔电的时刻（秒）")
    parser.add_argument("--delay-max", type=float, default=2.5, help="最晚提示拔电的时刻（秒）")
    parser.add_argument("--chunk-size", type=int, default=256)
    parser.add_argument("--csv", default="power_cut_result.csv")
    args = parser.parse_args()

    firmware = open(args.bin, "rb").read()
    print(f"固件: {args.bin}  {len(firmware)} 字节")
    print(f"计划: {args.runs} 次，提示时刻在 {args.delay_min}~{args.delay_max}s 之间随机")
    print(f"说明: 脚本喊'现在拔电'后拔掉 3.3V，【保持断电至少 1 秒】，等提示'插回电源'再插回并回车")
    print(f"      读超时 {PORT_TIMEOUT}s / START 等待上限 {START_DEADLINE}s / DATA 等待上限 {DATA_DEADLINE}s")

    link = SerialLink(args.port, args.baudrate)

    info = wait_bootloader(link, 3)
    if info is None:
        print("[FAIL] 连不上 Bootloader。请按一下复位键后 10 秒内重跑。")
        return 1
    if info["active"] == NONE:
        print("[FAIL] active=NONE，请先正常升级一次并 CONFIRM。")
        return 1

    rows = []
    ok = 0
    bad = 0

    for rnd in range(1, args.runs + 1):
        row = run_once(link, firmware, args, rnd)
        rows.append(row)
        if row["result"] == "OK":
            ok += 1
        elif row["result"] in ("FAIL", "DEAD"):
            bad += 1
        print(f"  [{rnd:2d}/{args.runs}] {row['phase']:12s} 断电@{row['cut_at']}s "
              f"反应{row['reaction_ms']}ms  {row['result']}  {row['note']}", flush=True)

    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["round", "phase", "cut_at",
                                               "reaction_ms", "result", "note"])
        writer.writeheader()
        writer.writerows(rows)

    total_valid = sum(1 for r in rows if r["result"] != "SKIP")
    erase_cnt = sum(1 for r in rows if r["result"] != "SKIP" and r["phase"].startswith("erase"))
    data_cnt = sum(1 for r in rows if r["result"] != "SKIP" and r["phase"] == "data")
    print(f"\n有效 {total_valid} 次：正确 {ok}，异常 {bad}")
    print(f"阶段分布：擦除/START {erase_cnt} 次，数据传输 {data_cnt} 次")
    if bad:
        print("异常明细:")
        for row in rows:
            if row["result"] in ("FAIL", "DEAD"):
                print(f"  第 {row['round']} 次: {row['note']}")
    print(f"结果已写入 {args.csv}")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

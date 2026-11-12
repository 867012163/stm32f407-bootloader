#!/usr/bin/env python3
"""元数据写入位置掉电注入测试。

原理：板子上烧 FaultInject 构建后，可以用 SET_FAULT 命令告诉 Bootloader
"写元数据写到第 N 个半字时立刻复位"。于是不用继电器、不用拔电，也能精确
地把"记录写了一半就断电"复现出来，逐个位置验证双份元数据能不能回退到上一版。

前提：
  1. 板子烧的是测试构建：cmake --preset FaultInject && cmake --build --preset FaultInject
  2. 板上元数据有效（先正常升级一次并 CONFIRM），否则首轮必然"两份都无效"

用法：
  python tool/fault_inject.py --port COM12 --bin firmware/LEDB.bin
  python tool/fault_inject.py --port COM12 --bin firmware/LEDB.bin --rounds 3
"""

import argparse
import csv
import os
import struct
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt  # noqa: E402

BL_CMD_SET_FAULT = 0x07
NONE = 0xFFFFFFFF
META_HALFWORDS = 32          # sizeof(BootMeta) / 2 = 64 / 2

SLOT_NAMES = {NONE: "NONE", 0: "A", 1: "B"}


def slot_name(value):
    return SLOT_NAMES.get(value, f"0x{value:08X}")


def read_info(ser):
    ser.reset_input_buffer()
    ser.write(qt.make_packet(qt.BL_CMD_INFO, 0))
    ser.flush()
    cmd, _seq, data = qt.recv_frame(ser)
    if cmd != qt.BL_CMD_INFO_RSP or len(data) != qt.INFO_PAYLOAD_LEN:
        raise RuntimeError(f"INFO 应答异常: cmd=0x{cmd:02X} len={len(data)}")
    keys = ["active", "pending", "rollback", "boot_count",
            "a_state", "b_state", "a_size", "b_size"]
    return dict(zip(keys, struct.unpack("<8I", data)))


def poll_info(ser, timeout=0.6):
    """短超时探一次，失败返回 None，用来轮询板子是否已经起来。"""
    old = ser.timeout
    ser.timeout = timeout
    try:
        return read_info(ser)
    except Exception:
        return None
    finally:
        ser.timeout = old


def wait_bootloader(ser, seconds=15.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        info = poll_info(ser)
        if info is not None:
            return info
        time.sleep(0.15)
    return None


def set_fault(ser, halfword_index):
    qt.send_packet(ser, BL_CMD_SET_FAULT, 0, struct.pack("<I", halfword_index))


def send_upgrade(ser, firmware, chunk=256):
    """发一次完整升级。注入触发时最后会超时，由调用方捕获。"""
    app_size = len(firmware)
    qt.send_packet(ser, qt.BL_CMD_START, 0,
                   struct.pack("<II", app_size, qt.crc32(firmware)))
    seq = 0
    for offset in range(0, app_size, chunk):
        qt.send_packet(ser, qt.BL_CMD_DATA, seq, firmware[offset:offset + chunk])
        seq += 1
    qt.send_packet(ser, qt.BL_CMD_END, 0)


def ensure_clean(ser):
    """把状态归一化到"没有待确认记录"，并返回当前状态。

    截断写入必须"看起来什么都没发生"，所以每个用例都要求前置状态干净；
    上一轮如果留下了 pending，用 CONFIRM 收掉它，再继续。
    """
    info = read_info(ser)
    if info["pending"] != NONE:
        qt.send_packet(ser, qt.BL_CMD_CONFIRM, 0)
        info = read_info(ser)
    return info


def run_case(ser, firmware, halfword_index, pre):
    """跑一个断点，返回 (是否通过, 说明)。

    判定用的是"和前置状态一致"，而不是写死 NONE：
    截断的写入必须被完全忽略，曾经是 A 就还是 A，曾经有 pending 就还是那个 pending。
    """
    baseline = pre["active"]
    pre_pending = pre["pending"]
    target = 1 if baseline == 0 else 0

    try:
        set_fault(ser, halfword_index)
    except RuntimeError:
        if wait_bootloader(ser, 12) is None:
            return False, "失去 Bootloader 响应（窗口过了？）"

    injected = False
    try:
        send_upgrade(ser, firmware)
    except RuntimeError:
        injected = True                      # END 未回 ACK = 注入已触发

    if halfword_index < META_HALFWORDS and not injected:
        return False, "注入未触发（记录完整写完了）"

    time.sleep(0.3)                          # 等 MCU 复位后重新进 Bootloader
    info = wait_bootloader(ser, 6)
    if info is None:
        return False, "复位后 Bootloader 无响应"

    if info["active"] != baseline:
        return False, f"active 被改坏: {slot_name(info['active'])} != {slot_name(baseline)}"

    if halfword_index < META_HALFWORDS:
        if info["pending"] != pre_pending:
            return (False,
                    f"状态被截断记录污染: pending={slot_name(info['pending'])}"
                    f" != {slot_name(pre_pending)}")
        return True, "回退到上一版"

    # N == 32：记录已写完，只是没来得及回 ACK，属于"已提交"
    if info["pending"] != target:
        return False, f"完整记录未生效: pending={slot_name(info['pending'])}"
    return True, "完整记录已提交"


def main():
    parser = argparse.ArgumentParser(description="元数据掉电注入测试")
    parser.add_argument("--port", default="COM12", help="串口，默认 COM12")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--bin", required=True, help="用来升级的固件 .bin")
    parser.add_argument("--rounds", type=int, default=1, help="每个断点重复几轮，默认 1")
    parser.add_argument("--max-halfword", type=int, default=META_HALFWORDS - 1,
                        help=f"最大断点下标，默认 {META_HALFWORDS - 1}；"
                             f"{META_HALFWORDS} 表示记录完整写完（会提交，慎用）")
    parser.add_argument("--csv", default="fault_inject_result.csv", help="结果 CSV 路径")
    args = parser.parse_args()

    firmware = open(args.bin, "rb").read()
    print(f"固件: {args.bin}  {len(firmware)} 字节")

    ser = serial.Serial(args.port, args.baudrate, timeout=3.0)
    time.sleep(0.2)

    info = wait_bootloader(ser, 3)
    if info is None:
        print("[FAIL] 连不上 Bootloader。请按一下板子复位键，然后 10 秒内重跑本脚本。")
        return 1

    if info["active"] == NONE:
        print("[FAIL] 当前 active=NONE（元数据已失效）。请先正常升级一次并 CONFIRM。")
        return 1

    # 若上一次测试留下了 pending，先收掉，保证从干净状态开始
    info = ensure_clean(ser)
    baseline = info["active"]
    print(f"基线: active={slot_name(baseline)}  pending={slot_name(info['pending'])}")

    # 预检：测试构建才认识 SET_FAULT；正式构建会回 NAK
    try:
        set_fault(ser, NONE)          # 0xFFFFFFFF = 关闭注入
    except RuntimeError:
        print("[FAIL] 板子不支持 SET_FAULT 命令 —— 烧的应该是正式构建。")
        print("       请烧 build/FaultInject/bootloaderf407.hex 后重试。")
        return 1
    print("预检通过：板子上是 FaultInject 测试构建")

    # 提醒：用的固件要和"非活动槽"的链接地址一致
    reset_vec = struct.unpack_from("<I", firmware, 4)[0]
    if 0x08010000 <= reset_vec < 0x08040000:
        bin_slot = 0
    elif 0x08040000 <= reset_vec < 0x08080000:
        bin_slot = 1
    else:
        bin_slot = None
        print(f"[WARN] 这个 bin 的复位向量 0x{reset_vec:08X} 不落在 A/B 任何一块里")

    target = 1 if baseline == 0 else 0
    if bin_slot is not None and target != bin_slot:
        print(f"[WARN] 目标槽是 {slot_name(target)}，但 --bin 是 {slot_name(bin_slot)} 区固件")

    if args.max_halfword >= META_HALFWORDS:
        print(f"[WARN] 断点 {META_HALFWORDS} = 记录完整写完，会真正提交 pending，"
              f"可能改变 active；之后的用例要求 --bin 与新的目标槽匹配")

    rows = []
    passed = 0
    total = 0
    aborted = False

    print(f"\n开始: {args.rounds} 轮 × 断点 0..{args.max_halfword}\n")
    for rnd in range(1, args.rounds + 1):
        for n in range(0, args.max_halfword + 1):
            pre = ensure_clean(ser)          # 每个用例都从"无 pending"开始
            if pre["active"] == NONE:
                print("[FAIL] 状态异常：active=NONE，测试中止")
                aborted = True
                break

            target = 1 if pre["active"] == 0 else 0
            if bin_slot is not None and target != bin_slot:
                print(f"[ABORT] 目标槽已变成 {slot_name(target)}，而 --bin 是 "
                      f"{slot_name(bin_slot)} 区固件；请换成对应固件重跑")
                aborted = True
                break

            if pre["active"] != baseline:
                baseline = pre["active"]
                print(f"  (active 变为 {slot_name(baseline)}，后续用例目标槽随之切换)")

            total += 1
            ok, note = run_case(ser, firmware, n, pre)
            passed += 1 if ok else 0
            rows.append({"round": rnd, "halfword": n, "ok": int(ok), "note": note})
            mark = "OK  " if ok else "FAIL"
            print(f"  [{total:3d}] round {rnd}  N={n:2d}  {mark}  {note}")

        if aborted:
            break

    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["round", "halfword", "ok", "note"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n合计 {total} 次，通过 {passed}，失败 {total - passed}")
    if aborted:
        print("[WARN] 测试提前中止（见上方 ABORT 原因），统计只包含已完成的用例")
    if total != passed:
        print("失败明细:")
        for row in rows:
            if not row["ok"]:
                print(f"  round {row['round']}  N={row['halfword']}: {row['note']}")
    print(f"结果已写入 {args.csv}")
    print("测试完成后记得把正式构建烧回去: "
          "cmake --build --preset Debug && STM32_Programmer_CLI -c port=SWD -w "
          "build/Debug/bootloaderf407.hex -v -rst")
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())

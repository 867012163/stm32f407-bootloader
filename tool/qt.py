import argparse
import struct
import sys
import time
import zlib

import serial


PORT = "COM12"
BAUDRATE = 115200
PACKET_MAX_DATA = 256
DEFAULT_CHUNK_SIZE = 256

HEAD = b"\x55\xAA"

BL_CMD_START = 0x00
BL_CMD_DATA = 0x01
BL_CMD_END = 0x02
BL_CMD_JUMP = 0x03
BL_CMD_ERASE = 0x04
BL_CMD_INFO = 0x05
BL_CMD_CONFIRM = 0x06

BL_ACK = b"\x06"
BL_NACK = b"\x15"


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def make_packet(cmd: int, seq: int = 0, data: bytes = b"") -> bytes:
    if not 0 <= cmd <= 0xFF:
        raise ValueError("cmd must fit in uint8")
    if not 0 <= seq <= 0xFFFF:
        raise ValueError("seq must fit in uint16")
    if len(data) > PACKET_MAX_DATA:
        raise ValueError(f"data length must be <= {PACKET_MAX_DATA}")

    body = struct.pack("<BHH", cmd, seq, len(data)) + data
    return HEAD + body + struct.pack("<I", crc32(body))


def read_ack(ser: serial.Serial, seq: int, cmd: int) -> None:
    ack = ser.read(1)
    if ack == BL_ACK:
        return
    if ack == BL_NACK:
        raise RuntimeError(f"packet failed: cmd=0x{cmd:02X}, seq={seq}, received NACK")
    raise RuntimeError(f"packet timeout/error: cmd=0x{cmd:02X}, seq={seq}, received={ack!r}")


def send_packet(ser: serial.Serial, cmd: int, seq: int = 0, data: bytes = b"") -> None:
    packet = make_packet(cmd, seq, data)
    ser.write(packet)
    ser.flush()
    read_ack(ser, seq, cmd)


def send_command(port: str, baudrate: int, cmd: int, name: str) -> None:
    with serial.Serial(port=port, baudrate=baudrate, timeout=2) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print(name)
        send_packet(ser, cmd, 0)

    print("命令发送完成")


def iter_firmware_chunks(firmware: bytes, chunk_size: int):
    if chunk_size <= 0 or chunk_size > PACKET_MAX_DATA:
        raise ValueError(f"chunk size must be 1..{PACKET_MAX_DATA}")
    if chunk_size % 2 != 0:
        raise ValueError("chunk size must be even; STM32F4 flash writes halfwords")

    app_size = len(firmware)
    for seq, offset in enumerate(range(0, app_size, chunk_size)):
        chunk = firmware[offset:offset + chunk_size]
        is_last = (offset + len(chunk)) >= app_size

        if not is_last and (len(chunk) % 2 != 0):
            raise ValueError("middle DATA packet length must be even")

        yield seq, offset, chunk, is_last


def send_firmware(port: str, baudrate: int, firmware: bytes, chunk_size: int, jump: bool) -> None:
    app_size = len(firmware)
    if app_size == 0:
        raise ValueError("firmware is empty")

    app_crc = crc32(firmware)
    start_data = struct.pack("<II", app_size, app_crc)

    with serial.Serial(port=port, baudrate=baudrate, timeout=2) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print(f"START size={app_size}, crc32=0x{app_crc:08X}")
        send_packet(ser, BL_CMD_START, 0, start_data)

        total_packets = (app_size + chunk_size - 1) // chunk_size
        for seq, offset, chunk, is_last in iter_firmware_chunks(firmware, chunk_size):
            tail = " last" if is_last else ""
            print(f"DATA {seq + 1}/{total_packets}, seq={seq}, offset=0x{offset:08X}, len={len(chunk)}{tail}")
            send_packet(ser, BL_CMD_DATA, seq, chunk)

        print("END")
        send_packet(ser, BL_CMD_END, 0)

        if jump:
            print("JUMP")
            send_packet(ser, BL_CMD_JUMP, 0)

    print("发送完成")


def load_firmware(path: str | None) -> bytes:
    if path is None:
        return bytes([i & 0xFF for i in range(DEFAULT_CHUNK_SIZE)])

    with open(path, "rb") as f:
        return f.read()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="STM32 bootloader UART upgrade tool")
    parser.add_argument("firmware", nargs="?", help="firmware .bin path; omitted means send test data")
    parser.add_argument("--port", default=PORT, help=f"serial port, default: {PORT}")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE, help=f"baudrate, default: {BAUDRATE}")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"DATA payload size, must be even and <= {PACKET_MAX_DATA}, default: {DEFAULT_CHUNK_SIZE}",
    )
    parser.add_argument("--jump", action="store_true", help="send JUMP after END succeeds")
    parser.add_argument("--confirm", action="store_true", help="send CONFIRM only; marks pending app as valid")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.confirm:
            send_command(args.port, args.baudrate, BL_CMD_CONFIRM, "CONFIRM")
        else:
            firmware = load_firmware(args.firmware)
            send_firmware(args.port, args.baudrate, firmware, args.chunk_size, args.jump)
    except serial.SerialException as exc:
        print(f"串口错误: {exc}")
        return 1
    except (RuntimeError, ValueError) as exc:
        print(f"升级失败: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

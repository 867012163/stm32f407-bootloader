# STM32F407 Bootloader with A/B Partition OTA

裸机 Bootloader：**自定义串口升级协议 + A/B 双分区 + 掉电安全回滚 + Cortex-M4 应用跳转**。

不用 RTOS，不依赖文件系统，全部逻辑跑在 512 KB Flash / 192 KB RAM 的 STM32F407VET6 上。
配套一个 Python 上位机（`tool/qt.py`），可以直接把 `.bin` 通过串口灌进板子并跳转。

<!-- 加上 CI 之后取消下面这行的注释
![build](https://github.com/867012163/stm32f407-bootloader/actions/workflows/build.yml/badge.svg)
-->

---

## 为什么做这个

很多裸机产品的升级方式是"把新固件直接覆盖当前固件"——擦除和写入要好几秒，一旦这期间掉电，
设备里就没有一份完整固件了，只能返厂。

这个项目要解决的就是这件事：**升级失败最坏的结果应该是"回到旧版本继续跑"，而不是变砖。**

做法是 A/B 双分区：新固件永远写"当前没在跑"的那块，写完校验通过再切换指针。

---

## 功能特性

| 功能 | 实现方式 |
| --- | --- |
| 串口升级协议 | 自定义二进制帧，`0x55AA` + 命令 + 序号 + 长度 + 数据 + CRC32 |
| 不定长收包 | `HAL_UARTEx_ReceiveToIdle_IT` 空闲中断，解析后回 ACK 并立即续接 |
| A/B 双分区 | 新固件写非活动分区，校验通过后切换元数据指针 |
| 掉电安全 | 元数据自带 magic + 版本 + 自身 CRC，读失败回落默认值 |
| 升级失败回滚 | `pending` 分区启动试运行，连续 3 次未确认则标记 `BAD` 并退回 `active` |
| 应用跳转 | 校验向量表与栈顶 → 关中断 → 反初始化外设 → 设 VTOR / MSP → 跳转 |
| 无命令自动启动 | 上电 10 s 内未收到 `START` 则直接跳 App |

---

## Flash 布局

| 区域 | 起始地址 | 大小 | 说明 |
| --- | --- | --- | --- |
| Bootloader | `0x08000000` | 32 KB | 本项目（2 个扇区） |
| Metadata | `0x08008000` | 16 KB | `BootMeta` 结构，单扇区 |
| Slot A | `0x0800C000` | 208 KB | 出厂固件 / 回退目标 |
| Slot B | `0x08040000` | 256 KB | 升级写入目标 |

两者相加正好 512 KB（`0x08000000` → `0x08080000`），没有留 scratch 扇区。

```
0x08000000 ┌──────────────┐
           │  Bootloader  │ 32 KB
0x08008000 ├──────────────┤
           │   Metadata   │ 16 KB
0x0800C000 ├──────────────┤
           │    Slot A    │ 208 KB
0x08040000 ├──────────────┤
           │    Slot B    │ 256 KB
0x08080000 └──────────────┘
```

---

## 升级协议

### 帧格式

```
 0      1      2      3-4      5-6      7 ... 7+len-1     末尾 4 字节
┌──────┬──────┬──────┬────────┬────────┬───────────────┬──────────────┐
│ 0x55 │ 0xAA │ cmd  │  seq   │  len   │     data      │    CRC32     │
└──────┴──────┴──────┴────────┴────────┴───────────────┴──────────────┘
  magic   1B     2B LE    2B LE    ≤256B          4B LE
```

- 单帧最长 `7 + 256 + 4 = 267` 字节，接收缓冲给到 512 字节（留"一次收到一帧半"的余量）。
- CRC32 的范围是 **`cmd` 开始到数据结束**（不含 `0x55AA`），多项式 `0xEDB88320`，初值 `0xFFFFFFFF`，结果取反。
- `DATA` 命令除最后一包外，数据长度必须为偶数——Flash 按字写入，半字不对齐会写失败。

### 命令表

| 命令 | 值 | 数据段 | 行为 |
| --- | --- | --- | --- |
| `START` | `0x00` | 8 B：`size` + `crc32`（各 4 B LE） | 选目标分区、擦除、进入升级态 |
| `DATA` | `0x01` | ≤256 B 固件数据 | 校验序号连续后写入，累积 CRC |
| `END` | `0x02` | 无 | 校验总长度与整包 CRC，写 `pending` 并保存元数据 |
| `JUMP` | `0x03` | 无 | 置跳转标志，主循环下一轮跳 App |
| `ERASE` | `0x04` | — | **占位实现，目前为空操作** |
| `INFO` | `0x05` | — | **占位实现，目前为空操作** |
| `CONFIRM` | `0x06` | 无 | `pending` 转正为 `active`，状态置 `VALID` |

应答：`0x06` = ACK，`0x15` = NAK，每帧一字节。

### 一次完整升级

```
上位机                                  Bootloader
  │  START(size, crc32)  ──────────────▶  擦目标分区，is_started = 1
  │  ◀────────────────────────────── ACK
  │  DATA(seq=0, 256B)   ──────────────▶  写入 + 累积 CRC
  │  ◀────────────────────────────── ACK
  │  ...（seq 必须连续，不连续直接 NAK）
  │  DATA(seq=n, 最后一包)
  │  ◀────────────────────────────── ACK
  │  END                 ──────────────▶  校验 size 与整包 CRC
  │  ◀────────────────────────────── ACK   写 pending_slot / rollback_slot
  │  JUMP                ──────────────▶  跳转新固件
  │                                      App 自检通过后发 CONFIRM → active 转正
```

---

## 元数据与状态机

```c
typedef struct {
    uint32_t magic;          // 0x424C4D54 ("BLMT")
    uint32_t version;        // 结构版本
    uint32_t active_slot;    // 当前运行的 slot
    uint32_t pending_slot;   // 待确认的 slot
    uint32_t rollback_slot;  // 可回退的 slot
    uint32_t slot_a_addr, slot_a_size, slot_a_crc, slot_a_state;
    uint32_t slot_b_addr, slot_b_size, slot_b_crc, slot_b_state;
    uint32_t boot_count;     // 试运行次数
    uint32_t meta_crc;       // 前面所有字段的 CRC32
} BootMeta;
```

分区状态：`EMPTY / VALID / TESTING / BAD`。

| 触发 | 状态变化 |
| --- | --- |
| `START` | 目标分区 `→ TESTING`，开始擦除 |
| `END` | `pending = 目标`，`rollback = 原 active`，`boot_count = 0` |
| 启动选到 `pending` | `boot_count++` 并保存；连续 3 次仍未 `CONFIRM` → 目标 `→ BAD`，退回 `active` |
| `CONFIRM` | `active = pending`，`pending = NONE`，目标 `→ VALID` |
| 上电读元数据 | 校验失败（magic / 版本 / 地址 / CRC 任一不符）→ 使用默认值 |

---

## 启动流程

```
main()
 ├─ HAL_Init() / 时钟 / GPIO / USART1
 ├─ BootMeta_Load()            // 读元数据，失败则用默认值
 ├─ Int_bootloader_init()      // 挂上 UART 空闲中断接收
 └─ while (1)
      ├─ Bootloader_ProcessPacket()      // 收齐一帧就解析并回 ACK/NAK
      ├─ 若未开始升级且无跳转请求，且已过 10 s
      │     └─ BootFlash_JumpToApp()
      └─ HAL_Delay(1)
```

跳转前的动作顺序：

```c
HAL_UART_Abort_IT(&huart1);  HAL_UART_DeInit(&huart1);   // 关掉用过的外设
__disable_irq();                                          // 关全局中断
SysTick->CTRL = 0; SysTick->LOAD = 0; SysTick->VAL = 0;   // 停 SysTick
SCB->VTOR = app_addr;                                     // 换向量表
__set_MSP(*(uint32_t *)app_addr);                         // 换栈
app_entry = (pFunction)(*(uint32_t *)(app_addr + 4));     // 复位向量
app_entry();
```

进入跳转前的合法性检查（`BootFlash_IsAppValid`）：

- 向量表第 0 字（初始 MSP）落在 `0x20000000 ~ 0x20020000`（SRAM 范围内）；
- 向量表第 1 字（复位向量）落在 `[slot_addr, slot_addr + APP_MAX_SIZE]` 内。

---

## 目录结构

```
Core/Src/
  main.c            CubeMX 生成 + 自加的启动主循环
  boot.c            ★ UART 空闲中断收包、帧分发、ACK/NAK
  protocol.c        ★ 帧解析、CRC32、命令处理
  metadata.c        ★ 元数据读写、校验、默认值、选槽
  flash_driver.c    ★ 擦除 / 写入 / 跳转 / 合法性检查
  usart.c gpio.c stm32f4xx_it.c ...   CubeMX 生成
Core/Inc/           ★ 对应的头文件 + config.h（Flash 布局与协议常量）
Drivers/            ST 官方 HAL 与 CMSIS（非自研）
tool/qt.py          ★ Python 上位机：打包、逐帧发送、等 ACK
cmake/              工具链与 CubeMX 集成
```

带 ★ 的 5 个文件是本项目自己写的，约 700 行；`Drivers/` 是 ST 生成的 HAL 库。

---

## 编译与烧录

需要 `arm-none-eabi-gcc`（>= 12）和 CMake + Ninja：

```bash
cmake --preset Debug
cmake --build build/Debug
# 产物：build/Debug/bootloaderf407.elf / .hex / .bin
```

烧录（任选其一）：

```bash
STM32_Programmer_CLI -c port=SWD -w build/Debug/bootloaderf407.hex -v -rst
# 或
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "program build/Debug/bootloaderf407.elf verify reset exit"
```

> App 需要单独编译，并把它的 Flash 起始地址改成 `0x0800C000`（Slot A）或 `0x08040000`（Slot B），
> 否则链接出来的向量表地址和 Bootloader 的期望对不上。

## 上位机

```bash
pip install pyserial
python tool/qt.py firmware.bin --port COM12 --baudrate 115200 --jump
python tool/qt.py --confirm --port COM12          # 只发 CONFIRM，把 pending 转正
```

---

## 已知限制（面试时可以直接讲）

1. **`ERASE` / `INFO` 是空实现**：命令能收、也回 ACK，但什么也不做。上位机如果依赖它们会误判。
2. **CRC 只校验接收到的数据，没有回读 Flash**：写入失败（比如扇区没擦干净）检测不出来。
3. **没有超时重传**：序号不连续直接 NAK，整次升级作废，必须从头再来；没有滑动窗口和位图。
4. **A/B 不等大**（208 KB / 256 KB）：从 B 回退到 A 时，如果新固件超过 208 KB 就退不回去。
5. **元数据只有一份**：写元数据中途掉电，只能靠自身 CRC 判"这次没写完"，没有双份备份。
6. **跳转顺序**：`__enable_irq()` 在 `app_entry()` 之前打开，理论上存在"App 还没建好向量表就进中断"的窗口。
7. **没有签名与加密**：不防篡改，属于"可靠性升级"而非"安全升级"。

## Roadmap

- [ ] 回读 Flash 计算整包 CRC（真正的写后校验）
- [ ] 元数据双份 + 递增序列号
- [ ] 序号位图 + 超时重传
- [ ] `INFO` 返回版本 / 分区 / 当前状态，配套上位机显示进度
- [ ] A/B 分区等大
- [ ] Ed25519 或 ECDSA 签名校验
- [ ] GitHub Actions 编译流水线

---

## License

MIT（或 Apache-2.0，提交前选定其一）。

`Drivers/` 下的 HAL 与 CMSIS 代码版权归 STMicroelectronics，遵循其原始许可。

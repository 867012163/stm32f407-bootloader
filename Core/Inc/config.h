#ifndef CONFIG_H
#define CONFIG_H

#define BL_PACKET_HEAD0         0x55
#define BL_PACKET_HEAD1         0xAA
#define BL_PACKET_MAX_DATA      256
#define BL_PACKET_HEADER_LEN    7
#define BL_PACKET_CRC_LEN       4
#define BL_PACKET_MIN_LEN       (BL_PACKET_HEADER_LEN + BL_PACKET_CRC_LEN)
#define BL_PACKET_MAX_LEN       (BL_PACKET_HEADER_LEN + BL_PACKET_MAX_DATA + BL_PACKET_CRC_LEN)
#define BL_PACKET_DATA_OFFSET   BL_PACKET_HEADER_LEN

#define BL_CMD_START            0x00
#define BL_CMD_DATA             0x01
#define BL_CMD_END              0x02
#define BL_CMD_JUMP             0x03
#define BL_CMD_ERASE            0x04
#define BL_CMD_INFO             0x05
#define BL_CMD_CONFIRM          0x06
#define BL_CMD_SET_FAULT        0x07   /* 仅 BL_FAULT_INJECT 测试构建可用 */

#define BL_START_DATA_LEN       8

#define BL_ACK                  0x06
#define BL_NACK                 0x15

/* INFO 应答：与请求同格式的响应帧，cmd = 0x85，载荷 32 字节 */
#define BL_CMD_INFO_RSP         0x85
#define BL_INFO_PAYLOAD_LEN     32

/* 元数据：双份，各独占一个扇区，写新份时只擦自己那一份 */
#define META_ADDR1          0x08008000U   /* 扇区 2 */
#define META_ADDR2          0x0800C000U   /* 扇区 3 */
#define META_SIZE           0x00004000U   /* 16 KB = 一个扇区 */

#define APP_ZONE_A          0x08010000U   /* 扇区 4(64K) + 扇区 5(128K) */
#define APP_ZONE_A_SIZE     0x00030000U   /* 192K */

#define APP_ZONE_B          0x08040000U   /* 扇区 6 + 扇区 7 */
#define APP_ZONE_B_SIZE     0x00040000U   /* 256K */

#define RX_BUF_LEN          512
#define UART_RECV_ONCE      RX_BUF_LEN

#define BOOT_MAX_RTY        3

#endif
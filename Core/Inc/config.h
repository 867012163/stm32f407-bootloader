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

#define BL_START_DATA_LEN       8

#define BL_ACK                  0x06
#define BL_NACK                 0x15

#define META_ADDR           0x08008000U
#define META_SIZE           0x00004000U

#define APP_ZONE_A          0x0800C000U
#define APP_ZONE_A_SIZE     0x00034000U //208K

#define APP_ZONE_B          0x08040000U
#define APP_ZONE_B_SIZE     0x00040000U

#define APP_MAX_SIZE        APP_ZONE_A_SIZE

#define RX_BUF_LEN          512
#define UART_RECV_ONCE      RX_BUF_LEN

#define BOOT_MAX_RTY        3

#endif
#ifndef METADATA_H
#define METADATA_H

#include "main.h"
#include <stdint.h>
#include <stdbool.h>

#define BOOT_META_MAGIC     0x424C4D54U  
#define BOOT_META_VERSION   2U

#define SLOT_NONE           0xFFFFFFFFU
#define SLOT_A              0x00000000U
#define SLOT_B              0x00000001U

#define SLOT_EMPTY          0xFFFFFFFFU
#define SLOT_VALID          0xA5A5A5A5U
#define SLOT_TESTING        0x5A5A5A5AU
#define SLOT_BAD            0x00000000U

typedef struct{
    uint32_t magic;           // 固定标记，判断 metadata 是否有效
    uint32_t version;         // metadata 结构版本
    uint32_t seq;             // 写入序号，双份之间选最新的一份
    uint32_t active_slot;     // 当前有效 APP：A 或 B
    uint32_t pending_slot;    // 待确认 APP：A 或 B，没有则为 NONE
    uint32_t rollback_slot;   // 回滚 APP：A 或 B，没有则为 NONE

    uint32_t slot_a_addr;     // A 区起始地址，可选，也可以写死在 config.h
    uint32_t slot_a_size;     // A 区固件大小
    uint32_t slot_a_crc;      // A 区固件 CRC32
    uint32_t slot_a_state;    // A 区状态：EMPTY / VALID / TESTING / BAD

    uint32_t slot_b_addr;
    uint32_t slot_b_size;
    uint32_t slot_b_crc;
    uint32_t slot_b_state;

    uint32_t boot_count;      // 可选，新 APP 试运行次数
    uint32_t meta_crc;        // metadata 自身 CRC，防止断电写坏
} BootMeta;

extern BootMeta meta;

void BootMeta_Load(void);
HAL_StatusTypeDef BootMeta_Save(void);
bool BootMeta_IsValid(const BootMeta *meta);
uint32_t Boot_GetWriteSlot(const BootMeta *meta);
uint32_t Boot_GetSlotAddr(uint32_t slot);
uint32_t Boot_GetActiveSlot(const BootMeta *meta);
uint32_t Boot_GetPendingSlot(const BootMeta *meta);

#ifdef BL_FAULT_INJECT
/* 测试用：把"写到第 N 个半字时复位"设置好，0xFFFFFFFF 表示关闭 */
void BootMeta_SetFault(uint32_t halfword_index);
#endif

#endif
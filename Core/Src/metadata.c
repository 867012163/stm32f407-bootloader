#include "metadata.h"
#include "config.h"
#include "protocol.h"
#include "stm32f4xx_hal_flash.h"
#include "stm32f4xx_hal_flash_ex.h"
#include <stddef.h>
#include <string.h>

BootMeta meta;


/* 两份元数据各占一个独立扇区：擦一份不会影响另一份 */
static const uint32_t meta_addr[2]   = { META_ADDR1, META_ADDR2 };
static const uint32_t meta_sector[2] = { FLASH_SECTOR_2, FLASH_SECTOR_3 };

#ifdef BL_FAULT_INJECT
/*
 * 掉电注入：写到第 fault_at_halfword 个半字时直接复位，模拟此刻断电。
 * 0xFFFFFFFF = 关闭。复位后 RAM 清零，注入点不会残留到下一次启动。
 */
static volatile uint32_t fault_at_halfword = 0xFFFFFFFFU;

void BootMeta_SetFault(uint32_t halfword_index)
{
    fault_at_halfword = halfword_index;
}
#endif

static uint32_t BootMeta_CalcCrc(const BootMeta *m)
{
    return BootProtocol_Crc32((const uint8_t *)m, offsetof(BootMeta, meta_crc));
}

bool BootMeta_IsValid(const BootMeta *m)
{
    if(m == NULL)
    {
        return false;
    }

    if(m -> magic != BOOT_META_MAGIC || m -> version != BOOT_META_VERSION)
    {
        return false;
    }

    if(m -> slot_a_addr != APP_ZONE_A || m -> slot_b_addr != APP_ZONE_B)
    {
        return false;
    }

    if(m -> active_slot != SLOT_A && m -> active_slot != SLOT_B && m -> active_slot != SLOT_NONE)
    {
        return false;
    }

    if(m -> pending_slot != SLOT_A && m -> pending_slot != SLOT_B && m -> pending_slot != SLOT_NONE)
    {
        return false;
    }

    return m -> meta_crc == BootMeta_CalcCrc(m);
}

/*
 * 选出最新的一份：返回 0 / 1 表示下标，-1 表示两份都无效。
 * 用有符号差值比较 seq，这样 seq 回绕（0xFFFFFFFF -> 0）时不会选错。
 */
static int BootMeta_PickNewest(void)
{
    const BootMeta *m0 = (const BootMeta *)meta_addr[0];
    const BootMeta *m1 = (const BootMeta *)meta_addr[1];
    bool v0 = BootMeta_IsValid(m0);
    bool v1 = BootMeta_IsValid(m1);

    if(v0 && v1)
    {
        return ((int32_t)(m0 -> seq - m1 -> seq) >= 0) ? 0 : 1;
    }
    if(v0)
    {
        return 0;
    }
    if(v1)
    {
        return 1;
    }

    return -1;
}

static void BootMeta_SetDefault(void)
{
    memset(&meta, 0xFF, sizeof(meta));

    meta.magic = BOOT_META_MAGIC;
    meta.version = BOOT_META_VERSION;
    meta.seq = 0;
    meta.active_slot = SLOT_NONE;
    meta.pending_slot = SLOT_NONE;
    meta.rollback_slot = SLOT_NONE;

    meta.slot_a_addr = APP_ZONE_A;
    meta.slot_a_size = 0;
    meta.slot_a_crc = 0xFFFFFFFFU;
    meta.slot_a_state = SLOT_EMPTY;

    meta.slot_b_addr = APP_ZONE_B;
    meta.slot_b_size = 0;
    meta.slot_b_crc = 0xFFFFFFFFU;
    meta.slot_b_state = SLOT_EMPTY;

    meta.boot_count = 0;
    meta.meta_crc = BootMeta_CalcCrc(&meta);
}

void BootMeta_Load(void)
{
    int idx = BootMeta_PickNewest();

    if(idx >= 0)
    {
        memcpy(&meta, (const void *)meta_addr[idx], sizeof(meta));
    }
    else
    {
        BootMeta_SetDefault();
    }
}

/* 擦掉目标副本所在扇区 -> 逐半字写入 -> 回读校验 */
static HAL_StatusTypeDef BootMeta_Write(uint32_t addr, uint32_t sector)
{
    HAL_StatusTypeDef ret;
    FLASH_EraseInitTypeDef erase = {0};
    uint32_t sector_error = 0;

    ret = HAL_FLASH_Unlock();
    if(ret != HAL_OK)
    {
        return ret;
    }

    erase.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase.Sector = sector;
    erase.NbSectors = 1;
    erase.Banks = FLASH_BANK_1;
    erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;

    ret = HAL_FLASHEx_Erase(&erase, &sector_error);
    if(ret != HAL_OK)
    {
        HAL_FLASH_Lock();
        return ret;
    }

    const uint8_t *p = (const uint8_t *)&meta;

    for(uint32_t i = 0; i < sizeof(BootMeta); i += 2U)
    {
#ifdef BL_FAULT_INJECT
        if((i / 2U) == fault_at_halfword)
        {
            HAL_FLASH_Lock();
            NVIC_SystemReset();     /* 模拟写出这一半时掉电 */
        }
#endif
        uint16_t halfword = p[i];

        if((i + 1U) < sizeof(BootMeta))
        {
            halfword |= ((uint16_t)p[i + 1U] << 8);
        }
        else
        {
            halfword |= 0xFF00U;
        }

        ret = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, addr + i, halfword);
        if(ret != HAL_OK)
        {
            break;
        }
    }

#ifdef BL_FAULT_INJECT
    if(fault_at_halfword == (uint32_t)(sizeof(BootMeta) / 2U))
    {
        HAL_FLASH_Lock();
        NVIC_SystemReset();         /* 最后一个半字写完的瞬间掉电 */
    }
#endif

    HAL_FLASH_Lock();

    if(ret == HAL_OK)
    {
        /* 回读确认：写进去的内容必须能读出且 CRC 正确 */
        const BootMeta *chk = (const BootMeta *)addr;
        if(!BootMeta_IsValid(chk) || chk -> seq != meta.seq)
        {
            ret = HAL_ERROR;
        }
    }

    return ret;
}

HAL_StatusTypeDef BootMeta_Save(void)
{
    int newest;
    int target;
    uint32_t seq;

    meta.magic = BOOT_META_MAGIC;
    meta.version = BOOT_META_VERSION;
    meta.slot_a_addr = APP_ZONE_A;
    meta.slot_b_addr = APP_ZONE_B;

    /* 状态与 size 保持一致，避免出现 "VALID 但 size=0" */
    if(meta.slot_a_size == 0U) meta.slot_a_state = SLOT_EMPTY;
    if(meta.slot_b_size == 0U) meta.slot_b_state = SLOT_EMPTY;

    newest = BootMeta_PickNewest();

    /* 永远写 "不是最新的那一份"，最新的那份保持完整，掉电可回退 */
    target = (newest == 0) ? 1 : 0;

    if(newest >= 0)
    {
        seq = ((const BootMeta *)meta_addr[newest]) -> seq + 1U;
        if(seq == 0U)
        {
            seq = 1U;   /* seq 回绕保护 */
        }
    }
    else
    {
        seq = 1U;
    }

    meta.seq = seq;
    meta.meta_crc = BootMeta_CalcCrc(&meta);

    return BootMeta_Write(meta_addr[target], meta_sector[target]);
}

uint32_t Boot_GetWriteSlot(const BootMeta *meta)
{
    
    if(meta -> active_slot == SLOT_A)
    {
        return SLOT_B;
    }
    if(meta -> active_slot == SLOT_B)
    {
        return SLOT_A;
    }
    
    return SLOT_A;//第一次默认写A
}

uint32_t Boot_GetSlotAddr(uint32_t slot)
{
    if (slot == SLOT_A)
    {
        return APP_ZONE_A;
    }
    if (slot == SLOT_B)
    {
        return APP_ZONE_B;
    }

    return 0;
}

uint32_t Boot_GetActiveSlot(const BootMeta *meta)
{
    return meta -> active_slot;
}

uint32_t Boot_GetPendingSlot(const BootMeta *meta)
{
    return meta -> pending_slot;
}

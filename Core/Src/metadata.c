#include "metadata.h"
#include "config.h"
#include "protocol.h"
#include "stm32f4xx_hal_flash.h"
#include <string.h>

BootMeta meta;

static uint32_t BootMeta_CalcCrc(const BootMeta *meta)
{
    return BootProtocol_Crc32((const uint8_t *)meta, offsetof(BootMeta, meta_crc));
}

bool BootMeta_IsValid(const BootMeta *meta)
{
    if(meta == NULL)
    {
        return false;
    }

    if(meta -> magic != BOOT_META_MAGIC || meta -> version != BOOT_META_VERSION)
    {
        return false;
    }

    if(meta -> slot_a_addr != APP_ZONE_A || meta -> slot_b_addr != APP_ZONE_B)
    {
        return false;
    }

    if(meta -> active_slot != SLOT_A && meta -> active_slot != SLOT_B && meta -> active_slot != SLOT_NONE)
    {
        return false;
    }

    if(meta -> pending_slot != SLOT_A && meta -> pending_slot != SLOT_B && meta -> pending_slot != SLOT_NONE)
    {
        return false;
    }

    return meta -> meta_crc == BootMeta_CalcCrc(meta);
}

static void BootMeta_SetDefault(void)
{
    memset(&meta, 0xFF, sizeof(meta));

    meta.magic = BOOT_META_MAGIC;
    meta.version = BOOT_META_VERSION;
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
    const BootMeta *flash_meta = (const BootMeta *)META_ADDR;

    if(BootMeta_IsValid(flash_meta))
    {
        memcpy(&meta, flash_meta, sizeof(meta));
    }
    else
    {
        BootMeta_SetDefault();
    }
}

HAL_StatusTypeDef BootMeta_Save(void)
{
    HAL_StatusTypeDef ret;
    uint32_t sector_error = 0;

    meta.magic = BOOT_META_MAGIC;
    meta.version = BOOT_META_VERSION;
    meta.slot_a_addr = APP_ZONE_A;
    meta.slot_b_addr = APP_ZONE_B;
    meta.meta_crc = BootMeta_CalcCrc(&meta);

    ret = HAL_FLASH_Unlock();
    if(ret != HAL_OK)
    {
        return ret;
    }

    FLASH_EraseInitTypeDef erase = {0};
    erase.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase.Sector = FLASH_SECTOR_2;
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
        uint16_t halfword = p[i];

        if((i + 1U) < sizeof(BootMeta))
        {
            halfword |= ((uint16_t)p[i + 1U] << 8);
        }
        else
        {
            halfword |= 0xFF00U;
        }

        ret = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, META_ADDR + i, halfword);
        if(ret != HAL_OK)
        {
            break;
        }
    }

    HAL_FLASH_Lock();
    return ret;
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
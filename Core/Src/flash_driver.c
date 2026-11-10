#include "flash_driver.h"
#include "protocol.h"
#include "config.h"
#include "stm32f4xx_hal_def.h"
#include "usart.h"
#include "metadata.h"

typedef void (*pFunction)(void);

uint32_t flash_write_offset = 0;
static uint32_t boot_slot = SLOT_NONE;
static uint32_t boot_addr;

uint32_t GetSector(uint32_t addr)
{
    if(addr < 0x08004000U) return FLASH_SECTOR_0;
    if(addr < 0x08008000U) return FLASH_SECTOR_1;
    if(addr < 0x0800C000U) return FLASH_SECTOR_2;
    if(addr < 0x08010000U) return FLASH_SECTOR_3;
    if(addr < 0x08020000U) return FLASH_SECTOR_4;
    if(addr < 0x08040000U) return FLASH_SECTOR_5;
    if(addr < 0x08060000U) return FLASH_SECTOR_6;
    return FLASH_SECTOR_7;
}

static bool SlotRangeOk(uint32_t slot, uint32_t offset, uint32_t len);

HAL_StatusTypeDef BootFlash_EraseApp(uint32_t app_size, uint32_t update_slot)
{
    if(app_size == 0 || !SlotRangeOk(update_slot, 0, app_size))
    {
        return HAL_ERROR;
    }

    HAL_StatusTypeDef ret = HAL_FLASH_Unlock();
    if(ret != HAL_OK)
    {
        return ret;
    }

    uint32_t update_addr = Boot_GetSlotAddr(update_slot);
    uint32_t start_sector = GetSector(update_addr);
    uint32_t end_sector = GetSector(update_addr + app_size - 1);

    FLASH_EraseInitTypeDef erase_init = {0};
    erase_init.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase_init.Sector = start_sector;
    erase_init.NbSectors = end_sector - start_sector + 1U;
    erase_init.Banks = FLASH_BANK_1;
    erase_init.VoltageRange = FLASH_VOLTAGE_RANGE_3;
    uint32_t sector_error = 0;

    ret = HAL_FLASHEx_Erase(&erase_init, &sector_error);
    
    HAL_FLASH_Lock();
    return ret;
}

HAL_StatusTypeDef BootFlash_Write(uint32_t offset, const uint8_t *data, uint32_t len, uint32_t update_slot)
{
    if(data == NULL || len == 0)
    {
        return HAL_ERROR;
    }
    
    if(!SlotRangeOk(update_slot, offset, len))
    {
        return HAL_ERROR;
    }
    
    uint32_t update_addr = Boot_GetSlotAddr(update_slot);
    uint32_t addr = update_addr + offset;

    if((addr % 2U) != 0U)
    {
        return HAL_ERROR;
    }

    HAL_StatusTypeDef ret = HAL_FLASH_Unlock();
    if(ret != HAL_OK)
    {
        return ret;
    }

    for(uint32_t i = 0; i < len; i += 2U)
    {
        uint16_t halfword = data[i];
        if((i + 1U) < len)
        {
            halfword |= ((uint16_t)data[i + 1U] << 8);
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

    HAL_FLASH_Lock();
    return ret;
}

static bool SlotRangeOk(uint32_t slot, uint32_t offset, uint32_t len)
{
    if(slot != SLOT_A && slot != SLOT_B) return false;
    uint32_t size = (slot == SLOT_A) ? APP_ZONE_A_SIZE : APP_ZONE_B_SIZE;
    if(offset > size || len > (size - offset)) return false;
    return true;
}

bool BootFlash_IsAppValid(uint32_t slot)
{  
    uint32_t addr = Boot_GetSlotAddr(slot);
    if(addr == 0)
    {
        return false;
    }
    
    uint32_t app_stack = *(uint32_t *)addr;
    uint32_t app_reset = *(uint32_t *)(addr + 4U);

    if(app_stack < 0x20000000U || app_stack > 0x20020000U)
    {
        return false;
    }

    if(app_reset < addr || app_reset > (addr + APP_MAX_SIZE))
    {
        return false;
    }

    return true;
}

HAL_StatusTypeDef BootFlash_SelectBootSlot(uint32_t *slot)
{
    if(slot == NULL)
    {
        return HAL_ERROR;
    }

    if(meta.pending_slot == SLOT_A || meta.pending_slot == SLOT_B)
    {
        if(!BootFlash_IsAppValid(meta.pending_slot) || meta.boot_count >= BOOT_MAX_RTY)
        {
            if(meta.pending_slot == SLOT_A)
            {
                meta.slot_a_state = SLOT_BAD;
            }
            else
            {
                meta.slot_b_state = SLOT_BAD;
            }

            meta.pending_slot = SLOT_NONE;
            meta.rollback_slot = SLOT_NONE;
            meta.boot_count = 0;
            BootMeta_Save();

            if(meta.active_slot == SLOT_A || meta.active_slot == SLOT_B)
            {
                *slot = meta.active_slot;
                return HAL_OK;
            }

            return HAL_ERROR;
        }

        meta.boot_count++;
        BootMeta_Save();
        *slot = meta.pending_slot;
        return HAL_OK;
    }

    if(meta.active_slot == SLOT_A || meta.active_slot == SLOT_B)
    {
        if(BootFlash_IsAppValid(meta.active_slot))
        {
            *slot = meta.active_slot;
            return HAL_OK;
        }
    }

    return HAL_ERROR;
}

HAL_StatusTypeDef BootFlash_JumpToApp(void)
{
    //判断APP是否有效
    if(BootFlash_SelectBootSlot(&boot_slot) != HAL_OK)
    {
        return HAL_ERROR;
    }
    //关闭使用的外设
    HAL_UART_Abort_IT(&huart1);
    HAL_UART_DeInit(&huart1);
    //关闭中断并清除Systick
    __disable_irq();

    SysTick -> CTRL = 0;
    SysTick -> LOAD = 0;
    SysTick -> VAL = 0;
    //设置向量表偏移
    boot_addr = Boot_GetSlotAddr(boot_slot);
    SCB -> VTOR = boot_addr;
    //设置MSP然后跳Reset_Handler
    
    uint32_t app_stack = *(uint32_t *)boot_addr;
    uint32_t app_reset = *(uint32_t *)(boot_addr + 4U);

    __set_MSP(app_stack);
    __enable_irq();
    pFunction app_entry = (pFunction)app_reset;
    app_entry();

    return HAL_OK;
}
#ifndef FLASH_DRIVER_H
#define FLASH_DRIVER_H

#include "main.h"
#include <stdint.h>
#include <stdbool.h>

HAL_StatusTypeDef BootFlash_EraseApp(uint32_t app_size, uint32_t update_slot);
HAL_StatusTypeDef BootFlash_Write(uint32_t offset, const uint8_t *data, uint32_t len, uint32_t update_slot);
bool BootFlash_IsAppValid(uint32_t slot);
bool BootFlash_VerifySlot(uint32_t slot);
HAL_StatusTypeDef BootFlash_JumpToApp(void);

#endif
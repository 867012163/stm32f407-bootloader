#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "main.h"
#include <stdint.h>
#include <stdbool.h>

extern uint32_t received_size;
extern bool boot_jump_pending;

uint32_t BootProtocol_Crc32(const uint8_t *data, uint32_t len);
uint32_t BootProtocol_Crc32Update(uint32_t crc, const uint8_t *data, uint32_t len);
bool BootProtocol_CheckCrc(const uint8_t *rx_buf, uint16_t rx_len);
HAL_StatusTypeDef BootProtocol_ParsePacket(const uint8_t *rx_buf, uint16_t rx_len);
bool BootProtocol_IsStarted(void);
uint16_t BootProtocol_TakeResponse(uint8_t *out, uint16_t out_max);

#endif
#ifndef BOOT_H
#define BOOT_H

#include "main.h"

extern volatile HAL_StatusTypeDef uart_rx_status;
extern volatile uint32_t uart_error_code;

void Int_bootloader_init(void);
void Bootloader_ProcessPacket(void);


#endif
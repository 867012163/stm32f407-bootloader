#ifndef BOOT_H
#define BOOT_H

#include "main.h"

extern volatile HAL_StatusTypeDef uart_rx_status;
extern volatile uint32_t uart_error_code;

/* 最近一次收到串口数据的时刻，用于真正的"空闲超时"判断 */
extern volatile uint32_t boot_last_activity_tick;

void Int_bootloader_init(void);
void Bootloader_ProcessPacket(void);


#endif
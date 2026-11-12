#include "boot.h"
#include "config.h"
#include "usart.h"
#include "protocol.h"
#include "flash_driver.h"

uint8_t rx_buf[RX_BUF_LEN];
volatile uint16_t rx_len = 0;
volatile uint8_t rx_ready = 0;
volatile uint32_t boot_last_activity_tick = 0;
volatile HAL_StatusTypeDef uart_rx_status = HAL_OK;
volatile uint32_t uart_error_code = HAL_UART_ERROR_NONE;
uint8_t tx_buf[64];


static void UART_StartReceive(void)
{
    uart_rx_status = HAL_UARTEx_ReceiveToIdle_IT(&huart1, rx_buf, UART_RECV_ONCE);
}

void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART1)
    {
        rx_len = Size;
        rx_ready = 1;
        boot_last_activity_tick = HAL_GetTick();   /* 有数据进来就刷新空闲计时 */
    }
}

void Bootloader_ProcessPacket(void)
{
    if(rx_ready == 1)
    {
        rx_ready = 0;
        HAL_StatusTypeDef ret = BootProtocol_ParsePacket(rx_buf, rx_len);
        uint16_t rsp_len = BootProtocol_TakeResponse(tx_buf, (uint16_t)sizeof(tx_buf));

        if(rsp_len > 0U)
        {
            /* INFO 等命令返回完整响应帧，不再单独回 ACK */
            HAL_UART_Transmit(&huart1, tx_buf, rsp_len, 200);
        }
        else
        {
            tx_buf[0] = (ret == HAL_OK) ? BL_ACK : BL_NACK;
            HAL_UART_Transmit(&huart1, tx_buf, 1, 100);
        }

        if(ret == HAL_OK && boot_jump_pending)
        {
            HAL_Delay(10);
            if(BootFlash_JumpToApp() != HAL_OK)
            {
                boot_jump_pending = false;     // 失败就回到正常等待
            }
        }

        UART_StartReceive();
    }
    
}

void Int_bootloader_init(void)
{
   boot_last_activity_tick = HAL_GetTick();
   UART_StartReceive();
    
}

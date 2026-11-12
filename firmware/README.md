# firmware

放 A/B 槽的测试固件（链接地址分别对应 `0x08010000` / `0x08040000`）。

`*.bin` 已在 `.gitignore` 中排除，不提交到仓库；需要时从对应工程重新生成：

```bash
arm-none-eabi-objcopy -O binary app.elf LEDA.bin
```

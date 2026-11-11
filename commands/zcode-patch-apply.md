---
description: 向 ZCode 客户端注入 TPS 状态栏补丁（注入前需完全退出 ZCode）
---

加载本插件自带的 `zcode-tokenspeed` 技能（`${CLAUDE_PLUGIN_ROOT}/skills/zcode-tokenspeed/SKILL.md`；若该变量未展开，就按插件安装目录下的同名路径读取），执行 TPS 状态栏注入：

```bash
python "<skill目录>/scripts/zcode_patcher.py" --tps-footer
```

要求：

1. 注入前先跑 `--tps-footer --check` 确认状态，并确认备份机制就绪（首次执行自动生成 `.tps.bak` 与 sidecar）。
2. 该补丁需要 ZCode **完全退出**（启动时才读 `app.asar`）；若用户不方便手动退出，可改用看护脚本 `scripts/apply_after_exit.py` 在退出后自动注入。
3. 把还原命令一并展示给用户保存，完成后提示重启 ZCode 验证：`--tps-footer --revert`。
4. 脚本报告异常时，**报告即结论**，不要绕过或手工改写文件。
5. 不确定会改什么时先加 `--dry-run` 预演（不写盘）；脚本会预检 ZCode 进程，运行中会直接拒绝（退出码 2）。

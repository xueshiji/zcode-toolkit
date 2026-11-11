---
description: 只读检查 ZCode 客户端 TPS 状态栏的注入状态（不修改任何文件）
---

加载本插件自带的 `zcode-tokenspeed` 技能（`${CLAUDE_PLUGIN_ROOT}/skills/zcode-tokenspeed/SKILL.md`；若该变量未展开，就按插件安装目录下的同名路径读取），按其「只读核实」步骤执行：

```bash
python "<skill目录>/scripts/zcode_patcher.py" --all --check
```

要求：

- **纯只读**，不要执行任何写入或 `--revert`。
- 用中文汇总状态：「已打 / 未打 / 含旧版组件 / 锚点失配」，失配时给出脚本报告的原因，不要绕过脚本的拒绝结论。
- 若提示「含旧版组件」，说明 app.asar 里的脚本比插件自带版本旧，需要完全退出 ZCode 后重跑注入。
